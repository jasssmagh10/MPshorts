from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests

MAX_TELEGRAM_BYTES = 49 * 1024 * 1024
MAX_TELEGRAM_MESSAGE_CHARS = 3900
MAX_YOUTUBE_DESCRIPTION_CHARS = 1800


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


def telegram_request(token: str, method: str, **kwargs: Any) -> dict[str, Any]:
    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        timeout=300,
        **kwargs,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise SystemExit(f"Telegram API error from {method}: {payload}")
    return payload


def metadata_text(metadata_path: Path) -> str:
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read MoneyPrinterTurbo metadata: {exc}") from exc

    script = str(payload.get("script", "")).strip() or "(script not found)"
    terms = payload.get("search_terms", "")
    if isinstance(terms, list):
        keywords = "\n".join(str(item).strip() for item in terms if str(item).strip())
    else:
        keywords = str(terms).strip() or "(keywords/search terms not found)"

    topic = os.environ.get("VIDEO_TOPIC", "Daily Short")
    return (
        f"Topic: {topic}\n\n"
        "SCRIPT USED\n"
        "==========\n"
        f"{script}\n\n"
        "KEYWORDS / PEXELS SEARCH TERMS USED\n"
        "====================================\n"
        f"{keywords}\n"
    )


def youtube_upload_command(metadata_path: Path, description_path: Path) -> str:
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    topic = os.environ.get("VIDEO_TOPIC", "Daily Short").strip()
    description = description_path.read_text(encoding="utf-8").strip()
    raw_terms = payload.get("search_terms", "")
    if isinstance(raw_terms, list):
        terms = [str(item).strip() for item in raw_terms if str(item).strip()]
    else:
        terms = [part.strip() for part in str(raw_terms).replace("\n", ",").split(",") if part.strip()]

    # The bot uses semicolons as field separators, so remove them from user-visible
    # values. Keep the command compact enough to copy from a Telegram message.
    clean_title = " ".join(topic.replace(";", " ").split())[:100]
    clean_tags = []
    for term in terms:
        # The upload bot expects every tag to be one word with no spaces.
        tag = re.sub(r"[^A-Za-z0-9]", "", term)
        if tag and tag.lower() not in {item.lower() for item in clean_tags}:
            clean_tags.append(tag[:80])
    tags = ",".join(clean_tags[:8])[:180]
    description = " ".join(description.replace(";", " ").split())[:MAX_YOUTUBE_DESCRIPTION_CHARS]
    status = os.environ.get("YT_DEFAULT_STATUS", "private").strip().lower()
    if status not in {"private", "unlisted", "public"}:
        status = "private"

    return (
        f"/yupload title={clean_title};tags={tags};status={status};"
        f"description={description}"
    )


def send_metadata(token: str, chat_id: str, metadata_path: Path, description_path: Path) -> None:
    text = metadata_text(metadata_path)
    command = youtube_upload_command(metadata_path, description_path)
    command_message = (
        "COPY-READY YOUTUBE COMMAND\n"
        "==========================\n"
        "Reply to the video with this command:\n\n"
        f"{command}"
    )
    if len(text) <= MAX_TELEGRAM_MESSAGE_CHARS:
        telegram_request(
            token,
            "sendMessage",
            data={"chat_id": chat_id, "text": text},
        )
        return

    document = metadata_path.with_name("short-metadata.txt")
    document.write_text(text, encoding="utf-8")
    with document.open("rb") as handle:
        telegram_request(
            token,
            "sendDocument",
            data={
                "chat_id": chat_id,
                "caption": "Script and keywords used for this Short",
            },
            files={"document": (document.name, handle, "text/plain")},
        )
    telegram_request(
        token,
        "sendMessage",
        data={"chat_id": chat_id, "text": command_message},
    )


def send_video(token: str, chat_id: str, video: Path, topic: str) -> None:
    with video.open("rb") as handle:
        telegram_request(
            token,
            "sendVideo",
            data={
                "chat_id": chat_id,
                "caption": f"Ready for review: {topic}",
                "supports_streaming": "true",
            },
            files={"video": (video.name, handle, "video/mp4")},
        )


def main() -> None:
    if len(sys.argv) not in {2, 4}:
        raise SystemExit(
            "Usage: python send_to_telegram.py video.mp4 [script.json description.txt]"
        )

    token = required("TELEGRAM_BOT_TOKEN")
    chat_id = required("TELEGRAM_CHAT_ID")
    topic = os.environ.get("VIDEO_TOPIC", "Daily Short")

    if len(sys.argv) == 2:
        video = compress_if_needed(Path(sys.argv[1]))
        send_video(token, chat_id, video, topic)
        print("Video sent to Telegram")
        return

    send_metadata(token, chat_id, Path(sys.argv[2]), Path(sys.argv[3]))
    print("Video, script, and keywords sent to Telegram")


if __name__ == "__main__":
    main()
