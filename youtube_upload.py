from __future__ import annotations

import datetime
import json
import os
import re
import sys
import traceback
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


def resolve_title(script_json: Path, fallback_topic: str) -> str:
    """Prefer AI-generated title from script.json. Fall back to topic."""
    try:
        payload = json.loads(script_json.read_text(encoding="utf-8"))
        ai_title = str(payload.get("title", "")).strip()
        if ai_title and 5 <= len(ai_title) <= 100:
            print(f"Using AI title: {ai_title!r}", file=sys.stderr)
            return ai_title[:95]
    except Exception as exc:
        print(f"Could not read AI title: {exc}", file=sys.stderr)

    topic = " ".join(fallback_topic.replace(";", " ").split())
    print(f"Falling back to topic as title: {topic!r}", file=sys.stderr)
    return topic[:95]


def compute_publish_at() -> str | None:
    """
    Return an ISO 8601 timestamp for scheduled publishing, or None if the
    video should upload with its normal status.

    Env vars:
      YT_PUBLISH_AT            — explicit ISO 8601 (e.g. "2026-09-25T04:30:00Z")
      YT_PUBLISH_OFFSET_HOURS  — hours from now; default 2 if YT_PUBLISH_AT unset
      YT_SCHEDULE              — "0"/"false"/"no" disables scheduling entirely
    """
    schedule_flag = os.environ.get("YT_SCHEDULE", "1").strip().lower()
    if schedule_flag in {"0", "false", "no", "off"}:
        return None

    explicit = os.environ.get("YT_PUBLISH_AT", "").strip()
    if explicit:
        return explicit

    try:
        offset_hours = float(os.environ.get("YT_PUBLISH_OFFSET_HOURS", "2"))
    except ValueError:
        offset_hours = 2.0

    now = datetime.datetime.now(datetime.timezone.utc)
    publish_at = now + datetime.timedelta(hours=offset_hours)
    return publish_at.isoformat(timespec="seconds").replace("+00:00", "Z")


def _run() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("Usage: python youtube_upload.py <video.mp4> <script.json> <description.txt>")

    video_path = Path(sys.argv[1])
    script_json = Path(sys.argv[2])
    description_path = Path(sys.argv[3])

    if not video_path.exists():
        raise SystemExit(f"Video not found: {video_path}")

    topic = os.environ.get("VIDEO_TOPIC", "Fact Factory Short")
    title = resolve_title(script_json, fallback_topic=topic)
    description = read_description(description_path)
    tags = build_tags(script_json)
    category = os.environ.get("YT_CATEGORY_ID", "27")

    # Default status. If publish_at is set, YouTube forces private anyway.
    status = os.environ.get("YT_UPLOAD_STATUS", "public").strip().lower()
    if status not in {"private", "unlisted", "public"}:
        status = "public"

    publish_at = compute_publish_at()

    # YouTube API requires privacyStatus=private when publishAt is set.
    if publish_at:
        status = "private"

    print(f"Uploading: {video_path.name}", file=sys.stderr)
    print(f"  Title: {title}", file=sys.stderr)
    print(f"  Status: {status}", file=sys.stderr)
    print(f"  Category: {category}", file=sys.stderr)
    print(f"  Tags: {tags}", file=sys.stderr)
    if publish_at:
        print(f"  Publish at: {publish_at}", file=sys.stderr)
    else:
        print(f"  Publish at: (immediate)", file=sys.stderr)

    youtube = build_youtube_client()

    status_block: dict = {
        "privacyStatus": status,
        "selfDeclaredMadeForKids": False,
    }
    if publish_at:
        status_block["publishAt"] = publish_at

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category,
        },
        "status": status_block,
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

    if publish_at:
        # Convert to IST for a friendlier Telegram message
        try:
            dt_utc = datetime.datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
            dt_ist = dt_utc.astimezone(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
            ist_str = dt_ist.strftime("%Y-%m-%d %H:%M IST")
        except Exception:
            ist_str = publish_at

        notify_telegram(
            f"✅ Uploaded to YouTube (scheduled)\n\n"
            f"{title}\n\n"
            f"{video_url}\n\n"
            f"Auto-publishes at: {ist_str}"
        )
    else:
        notify_telegram(
            f"✅ Uploaded to YouTube ({status})\n\n"
            f"{title}\n\n"
            f"{video_url}\n\n"
            f"Review in Studio → publish when ready."
        )


def main() -> None:
    try:
        _run()
    except Exception as exc:
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)

        topic = os.environ.get("VIDEO_TOPIC", "(unknown topic)")
        notify_telegram(
            f"❌ YouTube upload FAILED\n\n"
            f"Topic: {topic}\n\n"
            f"Error: {exc}\n\n"
            f"Check the workflow log in GitHub for the full traceback."
        )

        raise


if __name__ == "__main__":
    main()