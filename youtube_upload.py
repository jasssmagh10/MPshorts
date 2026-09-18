from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

import requests


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def notify_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"},
            timeout=30,
        )
    except Exception as exc:
        print(f"Telegram notify failed: {exc}", file=sys.stderr)


def build_youtube_client():
    creds = Credentials(
        token=None,
        refresh_token=required("YT_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=required("YT_CLIENT_ID"),
        client_secret=required("YT_CLIENT_SECRET"),
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def clean_tag(raw: str) -> str | None:
    tag = re.sub(r"[^A-Za-z0-9]", "", raw)
    return tag[:60] if tag else None


def build_tags(script_json: Path) -> list[str]:
    payload = json.loads(script_json.read_text(encoding="utf-8"))
    terms = payload.get("search_terms", "")
    if isinstance(terms, list):
        candidates = [str(t) for t in terms]
    else:
        candidates = re.split(r"[,\n]", str(terms))
    seen, out = set(), []
    for item in candidates:
        tag = clean_tag(item)
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
        if len(out) >= 10:
            break
    return out


def read_description(desc_path: Path) -> str:
    if not desc_path.exists():
        return ""
    return desc_path.read_text(encoding="utf-8").strip()[:4900]


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("Usage: python youtube_upload.py <video.mp4> <script.json> <description.txt>")

    video_path = Path(sys.argv[1])
    script_json = Path(sys.argv[2])
    description_path = Path(sys.argv[3])

    if not video_path.exists():
        raise SystemExit(f"Video not found: {video_path}")

    topic = os.environ.get("VIDEO_TOPIC", "Fact Factory Short")
    title = " ".join(topic.replace(";", " ").split())[:95]
    description = read_description(description_path)
    tags = build_tags(script_json)
    category = os.environ.get("YT_CATEGORY_ID", "27")
    status = os.environ.get("YT_UPLOAD_STATUS", "private").strip().lower()
    if status not in {"private", "unlisted", "public"}:
        status = "private"

    print(f"Uploading: {video_path.name}", file=sys.stderr)
    print(f"  Title: {title}", file=sys.stderr)
    print(f"  Status: {status}", file=sys.stderr)
    print(f"  Category: {category}", file=sys.stderr)
    print(f"  Tags: {tags}", file=sys.stderr)

    youtube = build_youtube_client()

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category,
        },
        "status": {
            "privacyStatus": status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 5,
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status_obj, response = request.next_chunk()
        if status_obj:
            pct = int(status_obj.progress() * 100)
            print(f"  Upload progress: {pct}%", file=sys.stderr)

    video_id = response.get("id", "")
    video_url = f"https://youtube.com/shorts/{video_id}"
    print(f"UPLOADED: {video_url}")

    notify_telegram(
        f"✅ Uploaded to YouTube (private)\n\n"
        f"{title}\n\n"
        f"{video_url}\n\n"
        f"Review in Studio → publish when ready."
    )


if __name__ == "__main__":
    main()