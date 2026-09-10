from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def replace_line(text: str, key: str, value: str) -> str:
    pattern = rf"(?m)^\s*{re.escape(key)}\s*=.*$"
    replacement = f'{key} = {value}'
    updated, count = re.subn(pattern, replacement, text, count=1)
    if count != 1:
        raise SystemExit(f"Could not find config key: {key}")
    return updated


def replace_commented_or_active_line(text: str, key: str, value: str) -> str:
    pattern = rf"(?m)^\s*#?\s*{re.escape(key)}\s*=.*$"
    replacement = f"{key} = {value}"
    updated, count = re.subn(pattern, replacement, text, count=1)
    if count != 1:
        raise SystemExit(f"Could not find config key: {key}")
    return updated


def main() -> None:
    mpt_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "mpt")
    example = mpt_dir / "config.example.toml"
    target = mpt_dir / "config.toml"
    if not example.exists():
        raise SystemExit(f"Missing {example}")

    shutil.copyfile(example, target)
    text = target.read_text(encoding="utf-8")

    gemini_key = required("GEMINI_API_KEY")
    pexels_key = required("PEXELS_API_KEY")

    text = replace_line(text, "llm_provider", '"gemini"')
    text = replace_line(text, "gemini_api_key", repr(gemini_key))
    text = replace_line(
        text,
        "gemini_model_name",
        repr(os.environ.get("GEMINI_MODEL") or "gemini-3.6-flash"),
    )
    text = replace_line(text, "video_source", '"pexels"')
    text = replace_line(text, "pexels_api_keys", f"[{pexels_key!r}]")
    text = replace_line(text, "subtitle_provider", '"edge"')
    text = replace_commented_or_active_line(text, "video_aspect_pexels", '"9:16"')
    text = replace_commented_or_active_line(text, "video_count", "1")
    text = replace_commented_or_active_line(text, "video_fit_mode", '"cover"')

    # These are "voiceover options" per MPT's own docs, which the CLI *does*
    # inherit from [ui] in config.toml (unlike video_count/video_aspect
    # above) — so setting them here is enough, no CLI flag needed.
    voice_name = os.environ.get("VOICE_NAME") or "en-US-GuyNeural"
    text = replace_commented_or_active_line(text, "voice_name", repr(voice_name))

    bgm_type = os.environ.get("BGM_TYPE") or "random"
    text = replace_commented_or_active_line(text, "bgm_type", repr(bgm_type))

    subtitle_position = os.environ.get("SUBTITLE_POSITION") or "bottom"
    text = replace_commented_or_active_line(text, "subtitle_position", repr(subtitle_position))

    subtitle_color = os.environ.get("SUBTITLE_COLOR") or "#FFFFFF"
    text = replace_commented_or_active_line(text, "text_fore_color", repr(subtitle_color))

    # Only touch the background box if a color was actually requested —
    # otherwise leave MPT's default (no background box) alone.
    subtitle_bg_color = os.environ.get("SUBTITLE_BG_COLOR", "").strip()
    if subtitle_bg_color:
        text = replace_commented_or_active_line(text, "subtitle_background_color", repr(subtitle_bg_color))
        text = replace_commented_or_active_line(text, "subtitle_background_enabled", "true")

    target.write_text(text, encoding="utf-8")
    print(f"Prepared {target}")


if __name__ == "__main__":
    main()
