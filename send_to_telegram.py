from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import requests

MAX_TELEGRAM_BYTES = 49 * 1024 * 1024


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def compress_if_needed(video: Path) -> Path:
    if video.stat().st_size <= MAX_TELEGRAM_BYTES:
        return video

    compressed = video.with_name(video.stem + "-telegram.mp4")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video),
            "-vf", "scale=1080:-2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-c:a", "aac", "-b:a", "96k",
            "-movflags", "+faststart", str(compressed),
        ],
        check=True,
    )
    if compressed.stat().st_size > MAX_TELEGRAM_BYTES:
        raise SystemExit("Compressed video is still too large for Telegram's Bot API upload limit")
    return compressed


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python send_to_telegram.py path/to/video.mp4")

    video = compress_if_needed(Path(sys.argv[1]))
    token = required("TELEGRAM_BOT_TOKEN")
    chat_id = required("TELEGRAM_CHAT_ID")
    topic = os.environ.get("VIDEO_TOPIC", "Daily Short")

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendVideo",
        data={
            "chat_id": chat_id,
            "caption": f"Ready for review: {topic}",
            "supports_streaming": "true",
        },
        files={"video": (video.name, video.open("rb"), "video/mp4")},
        timeout=300,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise SystemExit(f"Telegram API error: {payload}")
    print("Video sent to Telegram")


if __name__ == "__main__":
    main()
