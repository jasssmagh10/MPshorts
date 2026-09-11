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


# ---------- video send ----------

def compress_if_needed(video: Path) -> Path:
    if video.stat().st_size <= MAX_TELEGRAM_BYTES:
        return video

    compressed = video.with_name(video.stem + "-telegram.mp4")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video),
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


# ---------- sanitizers ----------

def clean_title(raw: str, limit: int = 100) -> str:
    return " ".join(raw.replace(";", " ").split())[:limit]


def clean_tag(raw: str) -> str | None:
    tag = re.sub(r"[^A-Za-z0-9]", "", raw)
    return tag[:MAX_TAG_LEN] if tag else None


def clean_hashtags(raw: str) -> str:
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


# ---------- AI tags with rotation ----------

def _tag_prompt(script: str) -> str:
    return (
        "Return ONLY comma-separated YouTube tags for the video below.\n"
        "Rules:\n"
        "- Each tag must be a SINGLE word: letters and digits only, no spaces, no hyphens, no underscores.\n"
        "- 6 to 8 tags.\n"
        "- No # symbol.\n"
        "- No semicolons.\n"
        "- No explanations, no quotes, no markdown.\n\n"
        f"Script:\n{script}"
    )


def _call_gemini_tags(prompt: str) -> str:
    api_key = required("GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": api_key},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"temperature": 0.3, "maxOutputTokens": 200}},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["candidates"][0]["content"]["parts"][0]["text"]


def _call_groq_tags(prompt: str) -> str:
    api_key = required("GROQ_API_KEY")
    model = os.environ.get("GROQ_MODEL") or "openai/gpt-oss-120b"
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model,
              "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.3},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _call_openrouter_tags(prompt: str) -> str:
    api_key = required("OPENROUTER_API_KEY")
    model = os.environ.get("OPENROUTER_MODEL") or "nvidia/nemotron-3-super-120b-a12b:free"
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model,
              "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.3},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


TAG_PROVIDERS = [
    ("gemini", _call_gemini_tags),
    ("groq", _call_groq_tags),
    ("openrouter", _call_openrouter_tags),
]


def ai_tags_from_script(script: str) -> list[str]:
    """Try each provider in order until one returns usable tags."""
    prompt = _tag_prompt(script)
    for name, fn in TAG_PROVIDERS:
        try:
            print(f"[tags] trying {name}", file=sys.stderr)
            raw = fn(prompt)
            tags = [t for t in (clean_tag(p) for p in raw.split(",")) if t]
            if tags:
                print(f"[tags] success with {name}: {tags}", file=sys.stderr)
                return tags
            print(f"[tags] {name} returned no usable tags", file=sys.stderr)
        except Exception as exc:
            print(f"[tags] {name} failed: {exc}", file=sys.stderr)
    return []


# ---------- build the /yupload command ----------

def build_upload_command(script_json: Path, description_path: Path) -> str:
    payload = json.loads(script_json.read_text(encoding="utf-8"))
    topic = os.environ.get("VIDEO_TOPIC", "Daily Short")
    title = clean_title(topic)

    # Description: read the file if it exists, otherwise build from topic.
    description = ""
    if description_path.exists():
        description_raw = description_path.read_text(encoding="utf-8").strip()
        description = clean_hashtags(description_raw)

    if not description:
        topic_words = re.findall(r"[A-Za-z0-9]+", topic)
        stop = {"the", "a", "an", "of", "in", "on", "to", "and", "or",
                "why", "how", "is", "are", "was", "were", "do", "does"}
        fallback = []
        seen = set()
        for word in topic_words:
            w = word.lower()
            if w in stop or len(w) < 3 or w in seen:
                continue
            seen.add(w)
            fallback.append("#" + word.capitalize())
        description = " ".join(fallback[:8]) or "#Shorts"

    # Tags: AI rotation, fall back to Pexels terms.
    script = str(payload.get("script", "")).strip()
    tags_list = ai_tags_from_script(script) if script else []
    if not tags_list:
        tags_list = [t for t in (clean_tag(x) for x in re.split(
            r"[,\n]", str(payload.get("search_terms", ""))
        )) if t]
    tags = ",".join(tags_list[:MAX_TAG_COUNT])[:180]

    status = os.environ.get("YT_DEFAULT_STATUS", "public").strip().lower()
    if status not in {"private", "unlisted", "public"}:
        status = "public"

    return (
        f"/yupload title={title};tags={tags};status={status};"
        f"description={description}"
    )


# ---------- telegram send of the command ----------

def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def send_upload_command(token: str, chat_id: str, command: str, topic: str) -> None:
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