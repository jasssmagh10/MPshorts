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
MAX_YOUTUBE_DESCRIPTION_CHARS = 1800
MAX_TAG_COUNT = 8
MAX_TAG_LEN = 80


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


# ---------- video send (unchanged behavior, fixed compression) ----------

def compress_if_needed(video: Path) -> Path:
    if video.stat().st_size <= MAX_TELEGRAM_BYTES:
        return video

    compressed = video.with_name(video.stem + "-telegram.mp4")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video),
            # 720p is plenty for a phone review copy; CRF 30 shrinks hard.
            "-vf", "scale=720:-2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
            "-c:a", "aac", "-b:a", "64k",
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


# ---------- field sanitizers ----------

def clean_title(raw: str, limit: int = 100) -> str:
    # No semicolons (bot separator). Collapse whitespace. Truncate.
    return " ".join(raw.replace(";", " ").split())[:limit]


def clean_tag(raw: str) -> str | None:
    # Bot requires single words, letters + digits only.
    tag = re.sub(r"[^A-Za-z0-9]", "", raw)
    return tag[:MAX_TAG_LEN] if tag else None


def clean_hashtags(raw: str) -> str:
    # Extract #Word tokens, drop duplicates, keep order.
    tokens = re.findall(r"#[A-Za-z0-9]+", raw)
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        key = token.lower()
        if key not in seen:
            seen.add(key)
            out.append(token)
    if not out:
        return ""
    return " ".join(out)[:MAX_YOUTUBE_DESCRIPTION_CHARS]


# ---------- gemini: script -> single-word tags ----------

def gemini_tags_from_script(script: str) -> list[str]:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return []
    model = os.environ.get("GEMINI_MODEL") or "gemini-3.6-flash"
    prompt = (
        "Return ONLY comma-separated YouTube tags for the video below.\n"
        "Rules:\n"
        "- Each tag must be a SINGLE word: letters and digits only, no spaces, no hyphens, no underscores.\n"
        "- 6 to 8 tags.\n"
        "- No # symbol.\n"
        "- No semicolons.\n"
        "- No explanations, no quotes, no markdown.\n\n"
        f"Script:\n{script}"
    )
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 200},
            },
            timeout=60,
        )
        response.raise_for_status()
        body = response.json()
        text = body["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as exc:
        print(f"Gemini tag generation failed (non-fatal): {exc}", file=sys.stderr)
        return []
    # Parse comma-separated, then sanitize.
    return [t for t in (clean_tag(p) for p in text.split(",")) if t]


# ---------- build the /yupload command ----------

def build_upload_command(script_json: Path, description_path: Path) -> str:
    payload = json.loads(script_json.read_text(encoding="utf-8"))
    topic = os.environ.get("VIDEO_TOPIC", "Daily Short")
    title = clean_title(topic)

    description_raw = description_path.read_text(encoding="utf-8").strip()
    description = clean_hashtags(description_raw)
    if not description:
        raise SystemExit("Description contains no usable hashtags")

    # Tags: prefer Gemini (single-word, on-topic), fall back to Pexels terms.
    script = str(payload.get("script", "")).strip()
    tags_list = gemini_tags_from_script(script) if script else []
    if not tags_list:
        tags_list = [t for t in (clean_tag(x) for x in re.split(
            r"[,\n]", str(payload.get("search_terms", ""))
        )) if t]
    tags = ",".join(tags_list[:MAX_TAG_COUNT])[:180]

    # Default is public now. Override with YT_DEFAULT_STATUS if you ever want it.
    status = os.environ.get("YT_DEFAULT_STATUS", "public").strip().lower()
    if status not in {"private", "unlisted", "public"}:
        status = "public"

    return (
        f"/yupload title={title};tags={tags};status={status};"
        f"description={description}"
    )


# ---------- telegram send of the command, in monospace ----------

def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def send_upload_command(token: str, chat_id: str, command: str, topic: str) -> None:
    # Reply hint: the user replies to the video message with this command.
    # <code> renders monospace and gives a tap-to-copy affordance.
    html = (
        f"<b>{escape_html(topic)}</b>\n"
        "Reply to the video above with this command:\n\n"
        f"<code>{escape_html(command)}</code>"
    )
    telegram_request(
        token,
        "sendMessage",
        data={
            "chat_id": chat_id,
            "text": html,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
    )


def send_script_dump(token: str, chat_id: str, script_json: Path) -> None:
    # Optional, only if SEND_SCRIPT_DUMP=1. Off by default.
    payload = json.loads(script_json.read_text(encoding="utf-8"))
    script = str(payload.get("script", "")).strip()
    terms = payload.get("search_terms", "")
    if isinstance(terms, list):
        terms = ", ".join(str(x) for x in terms)
    body = (
        f"SCRIPT\n======\n{script}\n\n"
        f"PEXELS TERMS\n============\n{terms}"
    )
    telegram_request(
        token,
        "sendMessage",
        data={
            "chat_id": chat_id,
            "text": f"<pre>{escape_html(body)}</pre>",
            "parse_mode": "HTML",
        },
    )


# ---------- entrypoint ----------

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

    script_json = Path(sys.argv[2])
    description_path = Path(sys.argv[3])

    if os.environ.get("SEND_SCRIPT_DUMP", "").strip() == "1":
        send_script_dump(token, chat_id, script_json)

    command = build_upload_command(script_json, description_path)
    send_upload_command(token, chat_id, command, topic)
    print("Upload command sent to Telegram")
    print(command)


if __name__ == "__main__":
    main()