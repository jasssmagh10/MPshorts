import json
import os
import subprocess
import numpy as np
from moviepy.editor import ImageClip, concatenate_videoclips, AudioFileClip, ColorClip, CompositeVideoClip
import requests

# --- CONFIGURATION ---
AUDIO_FILE = "master.mp3"          # change to "master.mp3" if that's what you have
IMAGE_FOLDER = "images"
TIMELINE_FILE = "timeline.json"
OUTPUT_FILE = "final_video_retro.mp4"
COMPRESSED_FILE = "final_video_telegram.mp4"
FPS = 24

# --- EFFECT SETTINGS ---
TRANSITION_DURATION = 0.7      # smooth crossfade
VIDEO_SIZE = (1280, 720)
ZOOM_STRENGTH = 0.15            # 15% — clearly visible Ken Burns
FLUTTER_AMPLITUDE = 10          # 10 px — visible jitter
FLUTTER_FREQ = 22               # 22 Hz — old projector speed
ALTERNATE_ZOOM = True           # alternate zoom-in / zoom-out per scene

# --- TELEGRAM ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def to_seconds(ts):
    parts = ts.split(":")
    return int(parts[0]) * 60 + float(parts[1])


def make_zoom_fn(zoom_in: bool, duration: float, strength: float):
    """Return a per-frame scale function for this clip."""
    if zoom_in:
        return lambda t, d=duration, z=strength: 1.0 + z * (t / d)
    return lambda t, d=duration, z=strength: 1.0 + z * (1.0 - t / d)


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

        # Alternate zoom direction so scenes feel different from each other
        zoom_in = (i % 2 == 0) if ALTERNATE_ZOOM else True

        # 1. Load image, resize constant to fit viewport (so zoom starts from a full frame)
        img = ImageClip(img_path).set_duration(duration)
        w, h = img.size
        target_ar = VIDEO_SIZE[0] / VIDEO_SIZE[1]
        src_ar = w / h
        if src_ar > target_ar:
            img = img.resize(height=VIDEO_SIZE[1])
        else:
            img = img.resize(width=VIDEO_SIZE[0])

        base_w = img.w
        base_h = img.h

        # 2. Animated zoom (per-frame resize)
        zoom_fn = make_zoom_fn(zoom_in, duration, ZOOM_STRENGTH)
        img = img.resize(zoom_fn)

        # 3. Flutter position — compute from BASE size * current zoom level
        def flutter_pos(t, bw=base_w, bh=base_h, zn=zoom_fn, d=duration):
            scale = zn(t)
            cur_w = bw * scale
            cur_h = bh * scale
            x = (VIDEO_SIZE[0] - cur_w) / 2 + FLUTTER_AMPLITUDE * np.sin(2 * np.pi * FLUTTER_FREQ * t)
            y = (VIDEO_SIZE[1] - cur_h) / 2 + (FLUTTER_AMPLITUDE * 0.6) * np.cos(2 * np.pi * FLUTTER_FREQ * t)
            return (x, y)

        img = img.set_position(flutter_pos)

        # 4. Layer on black background
        bg = ColorClip(size=VIDEO_SIZE, color=(0, 0, 0), duration=duration)
        composite = CompositeVideoClip([bg, img])

        # 5. Crossfades
        if i > 0:
            composite = composite.crossfadein(TRANSITION_DURATION)
        if i < len(timeline) - 1:
            composite = composite.crossfadeout(TRANSITION_DURATION)

        clips.append(composite)
        direction = "in" if zoom_in else "out"
        print(f"  [{i+1}/{len(timeline)}] {item['file']}  {duration:.2f}s  zoom-{direction}")

    print("Combining clips...")
    final_video = concatenate_videoclips(clips, padding=-TRANSITION_DURATION, method="compose")

    print(f"Adding audio: {AUDIO_FILE}")
    if not os.path.exists(AUDIO_FILE):
        raise SystemExit(f"Audio file not found: {AUDIO_FILE}")
    audio = AudioFileClip(AUDIO_FILE)
    final_video = final_video.set_audio(audio)

    print("Rendering video... this will take a while.")
    final_video.write_videofile(OUTPUT_FILE, fps=FPS, threads=4, preset="medium")
    print(f"Done! Saved as {OUTPUT_FILE}")

    # --- SEND TO TELEGRAM ---
    if not (TOKEN and CHAT_ID):
        print("Telegram secrets not set. Skipping upload.")
        return

    size_mb = os.path.getsize(OUTPUT_FILE) / (1024 * 1024)
    print(f"Rendered size: {size_mb:.1f} MB")

    send_file = OUTPUT_FILE
    if size_mb > 45:
        print("Compressing for Telegram...")
        subprocess.run([
            "ffmpeg", "-y", "-i", OUTPUT_FILE,
            "-vf", "scale=854:-2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
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
            data={"chat_id": CHAT_ID, "caption": "🎬 Final video"},
            files={"document": (os.path.basename(send_file), fh, "video/mp4")},
            timeout=600,
        )
    if response.status_code == 200:
        print("Sent to Telegram.")
    else:
        print(f"Upload failed: {response.status_code} {response.text[:300]}")


if __name__ == "__main__":
    main()