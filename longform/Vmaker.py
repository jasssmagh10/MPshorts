import json
import os
import subprocess
import numpy as np
from moviepy.editor import ImageClip, concatenate_videoclips, AudioFileClip
import requests

# --- CONFIGURATION ---
AUDIO_FILE = "master.mp3"          # change to "master.mp3" if that's what you have
IMAGE_FOLDER = "images"
TIMELINE_FILE = "timeline.json"
OUTPUT_FILE = "final_video_retro.mp4"
COMPRESSED_FILE = "final_video_telegram.mp4"
FPS = 24

# --- EFFECT SETTINGS ---
TRANSITION_DURATION = 1.0
VIDEO_SIZE = (1280, 720)           # 720p — faster render, smaller upload
ZOOM_STRENGTH = 0.05
FLUTTER_AMPLITUDE = 4
FLUTTER_FREQ = 15

# --- TELEGRAM SECRETS ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def to_seconds(ts):
    """Parse 'M:SS.ff' or 'M:SS' into float seconds."""
    parts = ts.split(":")
    minutes = int(parts[0])
    seconds = float(parts[1]) if len(parts) > 1 else 0.0
    return minutes * 60 + seconds


def make_flutter(clip_ref, duration):
    """Return a position function bound to this specific clip."""
    def flutter_pos(t):
        x = (VIDEO_SIZE[0] - clip_ref.w) / 2 + FLUTTER_AMPLITUDE * np.sin(2 * np.pi * FLUTTER_FREQ * t)
        y = (VIDEO_SIZE[1] - clip_ref.h) / 2 + (FLUTTER_AMPLITUDE * 0.5) * np.cos(2 * np.pi * FLUTTER_FREQ * t)
        return (x, y)
    return flutter_pos


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

        # 1. Load image, set duration
        clip = ImageClip(img_path).set_duration(duration)

        # 2. Ken Burns zoom
        clip = clip.resize(lambda t, d=duration: 1 + ZOOM_STRENGTH * (t / d))

        # 3. Fit to target height BEFORE positioning so flutter math is correct
        clip = clip.resize(height=VIDEO_SIZE[1])

        # 4. Flutter shake (closure binds to THIS clip via default arg)
        clip = clip.set_position(make_flutter(clip, duration))

        # 5. Crossfades
        if i > 0:
            clip = clip.crossfadein(TRANSITION_DURATION)
        if i < len(timeline) - 1:
            clip = clip.crossfadeout(TRANSITION_DURATION)

        clips.append(clip)
        print(f"  [{i+1}/{len(timeline)}] {item['file']}  {duration:.2f}s")

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

    # Telegram Bot API caps video at 50 MB. Compress if over.
    send_file = OUTPUT_FILE
    if size_mb > 45:
        print("Compressing for Telegram (target: <45 MB)...")
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
        print(f"Telegram upload failed: {response.status_code} {response.text[:300]}")


if __name__ == "__main__":
    main()