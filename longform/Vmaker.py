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
OVERLAY_FILE = "overlay.mp4"
BASE_RENDER_FILE = "temp_base.mp4"
FINAL_OUTPUT_FILE = "final_video.mp4"
FPS = 24

# --- EFFECT SETTINGS ---
TRANSITION_DURATION = 0.8
VIDEO_SIZE = (1280, 720)
ZOOM_STRENGTH = 0.20

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


def generate_procedural_scratch_overlay(output_path, duration=10, fps=24, size=(1280, 720)):
    """
    Synthesizes authentic vertical scratches and flickering dust particles
    matching the StockBox vintage screen overlay if no external file is provided.
    """
    print("✨ Generating procedural Scratch & Dust overlay...")
    w, h = size
    total_frames = int(duration * fps)

    cmd = [
        "ffmpeg", "-y", "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{w}x{h}", "-pix_fmt", "gray", "-r", str(fps),
        "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "ultrafast", output_path
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    rng = np.random.default_rng(101)

    scratch_x = rng.integers(100, w - 100)
    scratch_life = 0

    for _ in range(total_frames):
        frame = np.zeros((h, w), dtype=np.uint8)

        # 1. Dust & Dirt Flecks (flickering specks)
        num_dust = rng.integers(25, 60)
        dy = rng.integers(0, h, size=num_dust)
        dx = rng.integers(0, w, size=num_dust)
        frame[dy, dx] = rng.integers(160, 255, size=num_dust, dtype=np.uint8)

        # Clustered specks
        for _ in range(rng.integers(3, 8)):
            cy, cx = rng.integers(2, h - 3), rng.integers(2, w - 3)
            frame[cy:cy + 2, cx:cx + 2] = rng.integers(180, 255)

        # 2. Vertical Hairline Scratches (jittering lines)
        if scratch_life <= 0:
            if rng.random() < 0.60:
                scratch_x = rng.integers(60, w - 60)
                scratch_life = rng.integers(3, 14)
        else:
            scratch_life -= 1
            jx = int(scratch_x + rng.integers(-1, 2))
            if 0 <= jx < w:
                mask = rng.random(h) > 0.10
                frame[mask, jx] = rng.integers(170, 255)

        # Occasional faint second line
        if rng.random() < 0.30:
            sx2 = rng.integers(30, w - 30)
            frame[:, sx2] = rng.integers(120, 210)

        proc.stdin.write(frame.tobytes())

    proc.stdin.close()
    proc.wait()
    print("✅ Procedural overlay ready.")


def apply_screen_scratch_overlay(input_video, overlay_video, output_video):
    """
    Blends the scratch and dust overlay onto the video using Screen mode.
    Maintains a controlled 900 kbps bitrate (~35 MB for 5.5 minutes).
    """
    print(f"🎞️ Applying Scratch & Dust overlay via Screen blend mode from {overlay_video}...")

    filter_complex = (
        "[1:v]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720[ov];"
        "[0:v][ov]blend=all_mode='screen':all_opacity=0.85[v]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", input_video,
        "-stream_loop", "-1",
        "-i", overlay_video,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "0:a",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-b:v", "900k",
        "-maxrate", "1100k",
        "-bufsize", "2000k",
        "-c:a", "copy",
        "-shortest",
        "-movflags", "+faststart",
        output_video
    ]
    subprocess.run(cmd, check=True)
    print(f"✅ Master cut rendered to {output_video}")


def main():
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

    print(f"Attaching audio: {AUDIO_FILE}")
    if not os.path.exists(AUDIO_FILE):
        err = f"Audio file not found: {AUDIO_FILE}"
        send_telegram_alert(f"⚠️ Build Error: {err}")
        raise SystemExit(err)

    audio = AudioFileClip(AUDIO_FILE)
    final_video = final_video.set_audio(audio)

    # 1. Render clean base video
    print("Rendering base video...")
    final_video.write_videofile(
        BASE_RENDER_FILE,
        fps=FPS,
        threads=4,
        preset="ultrafast",
        bitrate="2200k",
        audio_codec="aac"
    )

    # 2. Check for overlay video or generate procedural loop
    overlay_to_use = OVERLAY_FILE
    if not os.path.exists(OVERLAY_FILE):
        generated_overlay = "generated_scratches.mp4"
        generate_procedural_scratch_overlay(generated_overlay, duration=10, fps=FPS, size=VIDEO_SIZE)
        overlay_to_use = generated_overlay

    # 3. Apply Screen blend overlay pass
    apply_screen_scratch_overlay(BASE_RENDER_FILE, overlay_to_use, FINAL_OUTPUT_FILE)

    if os.path.exists(BASE_RENDER_FILE):
        os.remove(BASE_RENDER_FILE)
    if os.path.exists("generated_scratches.mp4"):
        os.remove("generated_scratches.mp4")

    # 4. Telegram Delivery
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
                    "caption": f"🎬 <b>The Value Arc: Master Cut</b>\n\n✨ Film Scratch & Dust Overlay\n⏱️ Synced Timeline\n📦 File Size: {size_mb:.1f} MB"
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
