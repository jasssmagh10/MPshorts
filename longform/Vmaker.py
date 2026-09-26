import json
import os
import subprocess
import requests
import numpy as np
from moviepy.editor import (
    ImageClip, concatenate_videoclips, AudioFileClip, ColorClip, CompositeVideoClip
)
from faster_whisper import WhisperModel

# --- CONFIGURATION ---
AUDIO_FILE = "master.wav"
IMAGE_FOLDER = "images"
TIMELINE_FILE = "timeline.json"
BGM_FILE = "BGM1.mp3"
ASS_SUBTITLES_FILE = "subtitles.ass"

BASE_RENDER_FILE = "temp_base.mp4"
FINAL_OUTPUT_FILE = "final_video.mp4"
FPS = 24

# --- EFFECT & AUDIO SETTINGS ---
TRANSITION_DURATION = 0.8
VIDEO_SIZE = (1280, 720)
ZOOM_STRENGTH = 0.20
BGM_VOLUME = 0.08               
WORDS_PER_SUBTITLE_CHUNK = 3    
HIGHLIGHT_ACTIVE_WORD = False   
WHISPER_MODEL_SIZE = "small.en" 

# --- OVERLAY SETTINGS (Controlled via Environment Variables / CLI) ---
# Defaults are here, but you can override them when running the script:
# ENABLE_OVERLAY=True OVERLAY_TYPE=chromakey OVERLAY_OPACITY=0.25 python Vmaker.py
ENABLE_OVERLAY = os.environ.get("ENABLE_OVERLAY", "False").lower() == "true"
OVERLAY_TYPE = os.environ.get("OVERLAY_TYPE", "chromakey").lower()  # "chromakey" or "transparent"
OVERLAY_OPACITY = float(os.environ.get("OVERLAY_OPACITY", "0.25"))  # 0.0 to 1.0
OVERLAY_FILE = os.environ.get("OVERLAY_FILE", "overlay.mp4")

# --- TELEGRAM SECRETS ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def to_seconds(ts):
    parts = ts.split(":")
    return int(parts[0]) * 60 + float(parts[1])


def make_zoom_fn(zoom_in: bool, duration: float):
    if zoom_in:
        return lambda t, d=duration, z=ZOOM_STRENGTH: 1.0 + z * (t / d)
    return lambda t, d=duration, z=ZOOM_STRENGTH: 1.0 + z * (1.0 - t / d)


def make_position_fn(base_w, base_h, zoom_fn):
    def pos(t):
        scale = zoom_fn(t)
        cur_w = base_w * scale
        cur_h = base_h * scale
        x = (VIDEO_SIZE[0] - cur_w) / 2
        y = (VIDEO_SIZE[1] - cur_h) / 2
        return (x, y)
    return pos


def send_telegram_alert(message):
    if not (TOKEN and CHAT_ID):
        return
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=30,
        )
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}")


def generate_word_level_blowup_subtitles(audio_path, output_ass_path):
    print(f"🎙️ Transcribing audio with faster-whisper ({WHISPER_MODEL_SIZE})...")
    model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio_path, word_timestamps=True)

    words = []
    for segment in segments:
        for w in segment.words:
            clean = w.word.strip()
            if clean:
                words.append({
                    "word": clean,
                    "start": w.start,
                    "end": w.end
                })

    print(f"Captured {len(words)} individual words. Formatting blowup tags...")

    chunks = []
    for i in range(0, len(words), WORDS_PER_SUBTITLE_CHUNK):
        chunks.append(words[i:i + WORDS_PER_SUBTITLE_CHUNK])

    def format_ass_time(sec):
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        cs = int(round((sec - int(sec)) * 100))
        return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"

    active_color_tag = "\\c&H0000FFFF&" if HIGHLIGHT_ACTIVE_WORD else "\\c&H00FFFFFF&"

    ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,36,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.0,0.6,2,20,20,42,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for chunk in chunks:
        for idx, target_w in enumerate(chunk):
            start_t = target_w["start"]
            if idx < len(chunk) - 1:
                end_t = max(target_w["end"], chunk[idx + 1]["start"])
            else:
                end_t = target_w["end"] + 0.15

            start_str = format_ass_time(start_t)
            end_str = format_ass_time(end_t)

            line_parts = []
            for j, w in enumerate(chunk):
                if j == idx:
                    pop_tag = f"{{\\t(0,70,\\fscx125\\fscy125){active_color_tag}}}"
                    line_parts.append(f"{pop_tag}{w['word']}{{\\r}}")
                else:
                    line_parts.append(w["word"])

            full_text = " ".join(line_parts)
            events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{full_text}")

    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(ass_header + "\n".join(events))
    print(f"✅ Generated {len(events)} blowup subtitle cues in {output_ass_path}")


def apply_master_effects_and_audio(base_video, overlay_video, bgm_audio, ass_file, duration, output_video):
    print("🎞️ Assembling Final Master...")

    has_overlay = ENABLE_OVERLAY and os.path.exists(overlay_video)
    has_bgm = os.path.exists(bgm_audio)
    has_ass = os.path.exists(ass_file)

    if ENABLE_OVERLAY and not has_overlay:
        print(f"   -> ⚠️ ENABLE_OVERLAY is True, but {overlay_video} was not found! Skipping.")
    elif has_overlay:
        print(f"   -> Overlay is ENABLED (Mode: {OVERLAY_TYPE}, Opacity: {OVERLAY_OPACITY})")
    else:
        print("   -> Overlay is DISABLED (Skipping)")

    inputs = ["-i", base_video]
    input_idx = 1

    if has_overlay:
        inputs.extend(["-stream_loop", "-1", "-i", overlay_video])
        overlay_idx = input_idx
        input_idx += 1

    if has_bgm:
        inputs.extend(["-stream_loop", "-1", "-i", bgm_audio])
        bgm_idx = input_idx
        input_idx += 1

    # 1. Video Filters
    if has_overlay:
        if OVERLAY_TYPE == "chromakey":
            # FIXED: Using colorkey (RGB) with the exact HEX code from the overlay
            video_filters = (
                f"[0:v]format=yuv420p[base];"
                f"[{overlay_idx}:v]crop=iw*0.98:ih:iw*0.01:0,"
                f"scale=1280:720:force_original_aspect_ratio=increase,"
                f"crop=1280:720,format=rgba,"
                f"colorkey=0x0EB34B:0.30:0.10," 
                f"colorchannelmixer=aa={OVERLAY_OPACITY}[ov];"
                f"[base][ov]overlay=0:0:format=auto,format=yuv420p[v_graded]"
            )
        elif OVERLAY_TYPE == "transparent":
            video_filters = (
                f"[0:v]format=yuv420p[base];"
                f"[{overlay_idx}:v]scale=1280:720:force_original_aspect_ratio=increase,"
                f"crop=1280:720[ov];"
                f"[base][ov]overlay=0:0:alpha={OVERLAY_OPACITY},format=yuv420p[v_graded]"
            )
        else:
            print(f"   -> ⚠️ Unknown OVERLAY_TYPE '{OVERLAY_TYPE}'. Bypassing overlay.")
            video_filters = "[0:v]format=yuv420p[v_graded]"
        
        current_v = "[v_graded]"
    else:
        video_filters = "[0:v]format=yuv420p[v_graded]"
        current_v = "[v_graded]"

    if has_ass:
        ass_escaped = ass_file
        video_filters += f";{current_v}subtitles={ass_escaped}[v_final]"
        map_v = "[v_final]"
    else:
        map_v = current_v

    # 2. Audio Filters
    if has_bgm:
        fade_start = max(0, duration - 3.5)
        audio_filters = (
            f"[0:a]volume=1.0[narration];"
            f"[{bgm_idx}:a]volume={BGM_VOLUME},afade=t=out:st={fade_start:.2f}:d=3[bgm_low];"
            f"[narration][bgm_low]amix=inputs=2:duration=first:dropout_transition=2[a_final]"
        )
        filter_complex = f"{video_filters};{audio_filters}"
        map_a = "[a_final]"
    else:
        filter_complex = video_filters
        map_a = "0:a"

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", map_v,
        "-map", map_a,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-b:v", "900k",
        "-maxrate", "1100k",
        "-bufsize", "2000k",
        "-c:a", "aac",
        "-b:a", "128k",
        "-t", f"{duration:.3f}",
        "-movflags", "+faststart",
        output_video
    ]
    subprocess.run(cmd, check=True)
    print(f"✅ Final video rendered to {output_video}")


def main():
    if not os.path.exists(AUDIO_FILE):
        err = f"Audio file not found: {AUDIO_FILE}"
        send_telegram_alert(f"⚠️ Build Error: {err}")
        raise SystemExit(err)

    generate_word_level_blowup_subtitles(AUDIO_FILE, ASS_SUBTITLES_FILE)

    with open(TIMELINE_FILE, "r", encoding="utf-8") as f:
        timeline = json.load(f)["timeline"]

    print(f"Loaded timeline with {len(timeline)} entries")

    clips = []
    for i, item in enumerate(timeline):
        img_path = os.path.join(IMAGE_FOLDER, item["file"])
        if not os.path.exists(img_path):
            err = f"Missing image: {img_path}"
            send_telegram_alert(f"⚠️ Build Error: {err}")
            raise SystemExit(err)

        duration = to_seconds(item["end"]) - to_seconds(item["start"])
        if i < len(timeline) - 1:
            duration += TRANSITION_DURATION

        zoom_in = (i % 2 == 0)

        img = ImageClip(img_path).set_duration(duration)
        w, h = img.size
        target_ar = VIDEO_SIZE[0] / VIDEO_SIZE[1]
        src_ar = w / h
        if src_ar > target_ar:
            img = img.resize(height=VIDEO_SIZE[1])
        else:
            img = img.resize(width=VIDEO_SIZE[0])

        base_w, base_h = img.w, img.h

        zoom_fn = make_zoom_fn(zoom_in, duration)
        img = img.resize(zoom_fn)
        img = img.set_position(make_position_fn(base_w, base_h, zoom_fn))

        bg = ColorClip(size=VIDEO_SIZE, color=(0, 0, 0), duration=duration)
        composite = CompositeVideoClip([bg, img])

        if i > 0:
            composite = composite.crossfadein(TRANSITION_DURATION)
        if i < len(timeline) - 1:
            composite = composite.crossfadeout(TRANSITION_DURATION)

        clips.append(composite)

    print("Combining scene clips...")
    final_video = concatenate_videoclips(clips, padding=-TRANSITION_DURATION, method="compose")

    audio = AudioFileClip(AUDIO_FILE)
    total_duration = audio.duration

    final_video = final_video.set_audio(audio)

    print("Rendering base video with MoviePy...")
    final_video.write_videofile(
        BASE_RENDER_FILE,
        fps=FPS,
        threads=4,
        preset="ultrafast",
        bitrate="2200k",
        audio_codec="aac"
    )

    apply_master_effects_and_audio(
        base_video=BASE_RENDER_FILE,
        overlay_video=OVERLAY_FILE,
        bgm_audio=BGM_FILE,
        ass_file=ASS_SUBTITLES_FILE,
        duration=total_duration,
        output_video=FINAL_OUTPUT_FILE
    )

    if os.path.exists(BASE_RENDER_FILE):
        os.remove(BASE_RENDER_FILE)

    if not (TOKEN and CHAT_ID):
        print("Telegram secrets not configured. Skipping upload.")
        return

    size_mb = os.path.getsize(FINAL_OUTPUT_FILE) / (1024 * 1024)
    print(f"Final output size: {size_mb:.1f} MB")

    print(f"Uploading {FINAL_OUTPUT_FILE} ({size_mb:.1f} MB) to Telegram...")
    url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"

    try:
        with open(FINAL_OUTPUT_FILE, "rb") as fh:
            response = requests.post(
                url,
                data={
                    "chat_id": CHAT_ID,
                    "caption": f"🎬 <b>The Value Arc: Master Cut</b>\n\n✨ Overlay: {'Enabled (' + OVERLAY_TYPE + ')' if ENABLE_OVERLAY else 'Disabled'}\n💥 Word-by-Word Blowup Subtitles\n🎵 Looped Ambient BGM (-22 dB)\n📦 Size: {size_mb:.1f} MB"
                },
                files={"document": (FINAL_OUTPUT_FILE, fh, "video/mp4")},
                timeout=600,
            )

        if response.status_code == 200:
            print("🚀 Successfully sent to Telegram.")
        else:
            err = f"HTTP {response.status_code}: {response.text[:200]}"
            print(f"❌ Telegram upload failed: {err}")
            send_telegram_alert(f"❌ Telegram Upload Failed: {err}")
            raise SystemExit(err)

    except Exception as e:
        err = f"Upload error: {e}"
        print(f"❌ Telegram exception: {err}")
        send_telegram_alert(f"❌ Delivery Error: {err}")
        raise SystemExit(err)


if __name__ == "__main__":
    main()