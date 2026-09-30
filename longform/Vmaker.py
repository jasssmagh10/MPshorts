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
BGM_VOLUME = float(os.environ.get("BGM_VOLUME", "0.08"))
WORDS_PER_SUBTITLE_CHUNK = 3
HIGHLIGHT_ACTIVE_WORD = False

# Whisper model config (override via env if needed)
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "small")

# --- OVERLAY & PROCEDURAL EFFECT ROUTER ---
ENABLE_OVERLAY = os.environ.get("ENABLE_OVERLAY", "True").lower() == "true"
OVERLAY_TYPE = os.environ.get("OVERLAY_TYPE", "film_alive").lower()
OVERLAY_OPACITY = float(os.environ.get("OVERLAY_OPACITY", "0.35"))
OVERLAY_FILE = os.environ.get("OVERLAY_FILE", "overlay.mp4")
CUSTOM_FILTER = os.environ.get("CUSTOM_FILTER", "").strip()

# --- TELEGRAM SECRETS ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# ═══════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════
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


# ═══════════════════════════════════════════════════════════════════════════
# SUBTITLE GENERATION
# ═══════════════════════════════════════════════════════════════════════════
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


# ═══════════════════════════════════════════════════════════════════════════
# VIDEO FILTER BUILDER
# ═══════════════════════════════════════════════════════════════════════════
def build_video_filters(has_overlay_file, has_ass, overlay_idx, ass_file):
    """
    Returns (video_filters_str, map_v, mode_label).
    Uses iw (input width) and ih (input height) inside drawbox expressions —
    NOT w/h (box dimensions), which would collapse scratches to a 1px box.
    """
    alpha_w = f"{OVERLAY_OPACITY:.2f}"
    alpha_b = f"{min(1.0, OVERLAY_OPACITY + 0.10):.2f}"
    luma_grain = max(6, int(30 * OVERLAY_OPACITY))

    # Procedural scratch & dust chain — FIXED to use iw/ih
    scratches_chain = (
        f"drawbox=x='if(gt(sin(n*0.35), -0.3), iw*0.26 + sin(n*16)*2, -30)':y=0:w=1:h=ih:color=white@{alpha_w}:t=fill:eval=frame,"
        f"drawbox=x='if(gt(cos(n*0.55), -0.2), iw*0.76 + cos(n*21)*2, -30)':y=0:w=1:h=ih:color=black@{alpha_b}:t=fill:eval=frame,"
        f"drawbox=x='if(lt(random(1), 0.09), random(2)*iw, -30)':y=0:w=1:h=ih:color=white@{alpha_w}:t=fill:eval=frame,"
        f"drawbox=x='if(lt(random(3), 0.06), random(4)*iw, -30)':y=0:w=2:h=ih:color=black@{alpha_b}:t=fill:eval=frame,"
        f"drawbox=x='if(lt(random(5), 0.04), random(6)*iw, -30)':y='random(7)*ih':w=2:h=2:color=white@{alpha_w}:t=fill:eval=frame"
    )

    # ─── VIDEO ROUTER ──────────────────────────────────────────────────────
    if not ENABLE_OVERLAY:
        print("   -> Video grading / overlays: DISABLED")
        video_filters = "[0:v]format=yuv420p[v_graded]"
        mode_label = "OFF"

    elif OVERLAY_TYPE == "film_alive":
        print(f"   -> Mode [FILM_ALIVE]: Grain + Scratches + Dust + Micro-Breathing (Strength: {OVERLAY_OPACITY})")
        flicker_amp = 0.030 * OVERLAY_OPACITY
        contrast_amp = 0.040 * OVERLAY_OPACITY
        video_filters = (
            f"[0:v]format=yuv420p,"
            f"noise=c0s={luma_grain}:c0f=t+u,"
            f"eq=brightness='{flicker_amp:.5f}*(random(8)-0.5)':contrast='1+{contrast_amp:.5f}*(random(9)-0.5)',"
            f"{scratches_chain}[v_graded]"
        )
        mode_label = f"FILM_ALIVE (Str: {OVERLAY_OPACITY})"

    elif OVERLAY_TYPE == "scratches":
        print(f"   -> Mode [SCRATCHES]: Zero flicker, organic lines + dust (Opacity: {OVERLAY_OPACITY})")
        video_filters = (
            f"[0:v]format=yuv420p,"
            f"noise=c0s=7:c0f=t+u,"
            f"{scratches_chain}[v_graded]"
        )
        mode_label = f"SCRATCHES (Str: {OVERLAY_OPACITY})"

    elif OVERLAY_TYPE == "flicker":
        print(f"   -> Mode [FLICKER]: Exposure pulse + grain (Strength: {OVERLAY_OPACITY})")
        video_filters = (
            f"[0:v]format=yuv420p,"
            f"noise=c0s={luma_grain}:c0f=t+u,"
            f"eq=brightness='{0.08 * OVERLAY_OPACITY:.5f}*(random(1)-0.5)'"
            f":contrast='1+{0.10 * OVERLAY_OPACITY:.5f}*(random(2)-0.5)'"
            f":gamma_r=1.03:gamma_b=0.97[v_graded]"
        )
        mode_label = f"FLICKER (Str: {OVERLAY_OPACITY})"

    elif OVERLAY_TYPE == "chromakey" and has_overlay_file:
        print(f"   -> Mode [CHROMAKEY]: Keying {OVERLAY_FILE}")
        video_filters = (
            f"[0:v]format=yuv420p[base];"
            f"[{overlay_idx}:v]crop=iw*0.98:ih:iw*0.01:0,"
            f"scale=1280:720:force_original_aspect_ratio=increase,"
            f"crop=1280:720,format=rgba,"
            f"colorkey=0x0EB34B:0.30:0.10,"
            f"colorchannelmixer=aa={OVERLAY_OPACITY}[ov];"
            f"[base][ov]overlay=0:0:format=auto,format=yuv420p[v_graded]"
        )
        mode_label = f"CHROMAKEY (Str: {OVERLAY_OPACITY})"

    elif OVERLAY_TYPE == "transparent" and has_overlay_file:
        print(f"   -> Mode [TRANSPARENT]: Overlaying {OVERLAY_FILE}")
        video_filters = (
            f"[0:v]format=yuv420p[base];"
            f"[{overlay_idx}:v]scale=1280:720:force_original_aspect_ratio=increase,"
            f"crop=1280:720[ov];"
            f"[base][ov]overlay=0:0:alpha={OVERLAY_OPACITY},format=yuv420p[v_graded]"
        )
        mode_label = f"TRANSPARENT (Str: {OVERLAY_OPACITY})"

    elif OVERLAY_TYPE == "custom" and CUSTOM_FILTER:
        print(f"   -> Mode [CUSTOM]: Applying '{CUSTOM_FILTER}'")
        video_filters = f"[0:v]format=yuv420p,{CUSTOM_FILTER}[v_graded]"
        mode_label = "CUSTOM"

    else:
        print(f"   -> ⚠️ Mode '{OVERLAY_TYPE}' unknown or asset missing. Rendering clean base.")
        video_filters = "[0:v]format=yuv420p[v_graded]"
        mode_label = "FALLBACK_CLEAN"

    current_v = "[v_graded]"

    if has_ass:
        video_filters += f";{current_v}subtitles={ass_file}[v_final]"
        map_v = "[v_final]"
    else:
        map_v = current_v

    return video_filters, map_v, mode_label


# ═══════════════════════════════════════════════════════════════════════════
# MASTER VIDEO ASSEMBLY (primary attempt + clean fallback)
# ═══════════════════════════════════════════════════════════════════════════
def apply_master_effects_and_audio(base_video, overlay_video, bgm_audio, ass_file, duration, output_video):
    print("🎞️ Assembling Final Master Video Pipeline...")

    file_based_modes = ["chromakey", "transparent"]
    requires_overlay_file = ENABLE_OVERLAY and (OVERLAY_TYPE in file_based_modes)
    has_overlay_file = requires_overlay_file and os.path.exists(overlay_video)
    has_bgm = os.path.exists(bgm_audio)
    has_ass = os.path.exists(ass_file)

    inputs = ["-i", base_video]
    input_idx = 1
    overlay_idx = None

    if has_overlay_file:
        print(f"   -> Loading external overlay video: {overlay_video}")
        inputs.extend(["-stream_loop", "-1", "-i", overlay_video])
        overlay_idx = input_idx
        input_idx += 1
    elif requires_overlay_file and not has_overlay_file:
        print(f"   -> ⚠️ OVERLAY_TYPE is '{OVERLAY_TYPE}', but '{overlay_video}' was not found. Rendering clean output.")

    bgm_idx = None
    if has_bgm:
        inputs.extend(["-stream_loop", "-1", "-i", bgm_audio])
        bgm_idx = input_idx
        input_idx += 1

    # ═══════════════════════════════════════════════════════════════════════
    # ATTEMPT 1 — Full procedural filter chain
    # ═══════════════════════════════════════════════════════════════════════
    video_filters, map_v, mode_label = build_video_filters(has_overlay_file, has_ass, overlay_idx, ass_file)

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
        "-b:v", "1200k",
        "-maxrate", "1400k",
        "-bufsize", "2400k",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-t", f"{duration:.3f}",
        "-movflags", "+faststart",
        output_video
    ]

    primary_succeeded = False
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"✅ Final video rendered to {output_video} [mode: {mode_label}]")
        primary_succeeded = True
        return mode_label
    except subprocess.CalledProcessError as e:
        err_text = (e.stderr or b"").decode(errors="ignore")[:400]
        print("⚠️ Procedural render FAILED. FFmpeg stderr:")
        print(f"   {err_text}")
        print("   → Falling back to clean render (subtitles + BGM only)...")

    # ═══════════════════════════════════════════════════════════════════════
    # ATTEMPT 2 — Clean fallback (guaranteed-labeled filter pads)
    # ═══════════════════════════════════════════════════════════════════════
    if has_ass:
        fallback_v = f"[0:v]format=yuv420p,subtitles={ass_file}[v_fallback]"
    else:
        fallback_v = "[0:v]format=yuv420p[v_fallback]"
    fallback_map_v = "[v_fallback]"

    if has_bgm:
        fade_start = max(0, duration - 3.5)
        fallback_a = (
            f"[0:a]volume=1.0[narration];"
            f"[{bgm_idx}:a]volume={BGM_VOLUME},afade=t=out:st={fade_start:.2f}:d=3[bgm_low];"
            f"[narration][bgm_low]amix=inputs=2:duration=first:dropout_transition=2[a_final]"
        )
        fallback_complex = f"{fallback_v};{fallback_a}"
        fallback_map_a = "[a_final]"
    else:
        fallback_complex = fallback_v
        fallback_map_a = "0:a"

    fallback_cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", fallback_complex,
        "-map", fallback_map_v,
        "-map", fallback_map_a,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-b:v", "1200k",
        "-maxrate", "1400k",
        "-bufsize", "2400k",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-t", f"{duration:.3f}",
        "-movflags", "+faststart",
        output_video
    ]

    try:
        subprocess.run(fallback_cmd, check=True, capture_output=True)
        print("✅ Final video rendered (FALLBACK MODE, clean output)")
        send_telegram_alert(
            "⚠️ <b>Procedural overlay failed — clean render used.</b>\n"
            "Subtitles and BGM were applied, but procedural film effect was bypassed."
        )
        return "FALLBACK_CLEAN"
    except subprocess.CalledProcessError as e:
        err_text = (e.stderr or b"").decode(errors="ignore")[:400]
        send_telegram_alert(
            f"❌ <b>Both renders failed.</b>\n\n"
            f"<code>{err_text}</code>\n\n"
            f"Manual investigation required."
        )
        print(f"❌ Fallback render also FAILED: {err_text}")
        raise


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════
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

    mode_label = apply_master_effects_and_audio(
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
                    "caption": (
                        f"🎬 <b>The Value Arc: Master Cut</b>\n\n"
                        f"✨ Effect/Overlay: {mode_label}\n"
                        f"💥 Word-by-Word Subtitles: Active\n"
                        f"🎵 Looped Ambient BGM\n"
                        f"📦 Size: {size_mb:.1f} MB"
                    )
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