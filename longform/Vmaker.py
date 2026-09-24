import json
import os
import subprocess
import requests
import numpy as np
from moviepy.editor import ImageClip, concatenate_videoclips, AudioFileClip, ColorClip, CompositeVideoClip

# --- CONFIGURATION ---
AUDIO_FILE = "master.mp3"
IMAGE_FOLDER = "images"
TIMELINE_FILE = "timeline.json"
BASE_RENDER_FILE = "base_video.mp4"
OUTPUT_FILE = "final_video_retro.mp4"
COMPRESSED_FILE = "final_video_telegram.mp4"
FPS = 24

# --- EFFECT SETTINGS ---
TRANSITION_DURATION = 0.8
VIDEO_SIZE = (1280, 720)
ZOOM_STRENGTH = 0.18            # Ken Burns zoom depth

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


def apply_retro_effects_ffmpeg(input_video, output_video):
    """
    Applies authentic retro documentary styling via native FFmpeg:
    1. noise: Dynamic fine film grain that animates on every frame.
    2. eq: Real analog projector bulb luminance flicker.
    3. vignette: Cinematic corner shading.
    """
    print("🎞️ Applying Film Grain, Projector Flicker & Retro Aesthetics via FFmpeg...")
    
    # Filter breakdown:
    # - noise=alls=14:allf=t+u -> temporal & uniform film grain
    # - eq=brightness='(random(0)-0.5)*0.05':contrast=1.04 -> 5% random exposure flicker per frame
    # - vignette=angle=PI/4 -> soft retro lens darkening
    vf_filter = (
        "noise=alls=14:allf=t+u,"
        "eq=brightness='(random(0)-0.5)*0.05':contrast=1.04:saturation=1.05,"
        "vignette=angle=PI/4"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", input_video,
        "-vf", vf_filter,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "19",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_video
    ]
    subprocess.run(cmd, check=True)
    print(f"✅ Retro effect rendered to {output_video}")


def main():
    with open(TIMELINE_FILE, "r", encoding="utf-8") as f:
        timeline = json.load(f)["timeline"]

    print(f"Loaded timeline with {len(timeline)} entries")

    clips = []
    for i, item in enumerate(timeline):
        img_path = os.path.join(IMAGE_FOLDER, item["file"])
        if not os.path.exists(img_path):
            raise SystemExit(f"Missing image: {img_path}")

        duration = to_seconds(item["end"]) - to_seconds(item["start"])
        if i < len(timeline) - 1:
            duration += TRANSITION_DURATION

        zoom_in = (i % 2 == 0)

        # 1. Load image and fill viewport
        img = ImageClip(img_path).set_duration(duration)
        w, h = img.size
        target_ar = VIDEO_SIZE[0] / VIDEO_SIZE[1]
        src_ar = w / h
        if src_ar > target_ar:
            img = img.resize(height=VIDEO_SIZE[1])
        else:
            img = img.resize(width=VIDEO_SIZE[0])

        base_w, base_h = img.w, img.h

        # 2. Ken Burns Zoom & Centering
        zoom_fn = make_zoom_fn(zoom_in, duration)
        img = img.resize(zoom_fn)
        img = img.set_position(make_position_fn(base_w, base_h, zoom_fn))

        # 3. Backdrop & Crossfades
        bg = ColorClip(size=VIDEO_SIZE, color=(0, 0, 0), duration=duration)
        composite = CompositeVideoClip([bg, img])

        if i > 0:
            composite = composite.crossfadein(TRANSITION_DURATION)
        if i < len(timeline) - 1:
            composite = composite.crossfadeout(TRANSITION_DURATION)

        clips.append(composite)
        direction = "in" if zoom_in else "out"
        print(f"  [{i+1}/{len(timeline)}] {item['file']}  {duration:.2f}s  zoom-{direction}")

    print("Combining scene clips...")
    final_video = concatenate_videoclips(clips, padding=-TRANSITION_DURATION, method="compose")

    print(f"Attaching audio: {AUDIO_FILE}")
    if not os.path.exists(AUDIO_FILE):
        raise SystemExit(f"Audio file not found: {AUDIO_FILE}")
    audio = AudioFileClip(AUDIO_FILE)
    final_video = final_video.set_audio(audio)

    # 4. Render intermediate clean cut
    print("Rendering base video with MoviePy...")
    final_video.write_videofile(
        BASE_RENDER_FILE,
        fps=FPS,
        threads=4,
        preset="ultrafast",
        audio_codec="aac"
    )

    # 5. Apply native Retro Film & Flicker pass
    apply_retro_effects_ffmpeg(BASE_RENDER_FILE, OUTPUT_FILE)

    if os.path.exists(BASE_RENDER_FILE):
        os.remove(BASE_RENDER_FILE)

    # 6. Telegram delivery
    if not (TOKEN and CHAT_ID):
        print("Telegram credentials not configured. Skipping upload.")
        return

    size_mb = os.path.getsize(OUTPUT_FILE) / (1024 * 1024)
    print(f"Rendered size: {size_mb:.1f} MB")

    send_file = OUTPUT_FILE
    if size_mb > 45:
        print("Compressing video for Telegram 50MB ceiling...")
        subprocess.run([
            "ffmpeg", "-y", "-i", OUTPUT_FILE,
            "-vf", "scale=854:-2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-c:a", "aac", "-b:a", "96k",
            "-movflags", "+faststart",
            COMPRESSED_FILE,
        ], check=True)
        send_file = COMPRESSED_FILE
        size_mb = os.path.getsize(send_file) / (1024 * 1024)
        print(f"Compressed size: {size_mb:.1f} MB")

    print("Uploading to Telegram...")
    url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"
    with open(send_file, "rb") as fh:
        response = requests.post(
            url,
            data={"chat_id": CHAT_ID, "caption": "🎬 The Value Arc: Master Cut (Retro Film Grade)"},
            files={"document": (os.path.basename(send_file), fh, "video/mp4")},
            timeout=600,
        )
    if response.status_code == 200:
        print("🚀 Successfully sent to Telegram.")
    else:
        print(f"Upload failed: {response.status_code} {response.text[:300]}")


if __name__ == "__main__":
    main()
