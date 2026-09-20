from __future__ import annotations

import json
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


def apply_llm_provider(text: str) -> str:
    provider = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()

    if provider == "groq":
        text = replace_line(text, "llm_provider", '"groq"')
        text = replace_line(text, "groq_api_key", json.dumps(required("GROQ_API_KEY")))
        text = replace_line(
            text, "groq_model_name",
            json.dumps(os.environ.get("GROQ_MODEL") or "openai/gpt-oss-120b"),
        )

    elif provider == "openrouter":
        text = replace_line(text, "llm_provider", '"openrouter"')
        text = replace_line(text, "openrouter_api_key", json.dumps(required("OPENROUTER_API_KEY")))
        text = replace_line(
            text, "openrouter_model_name",
            json.dumps(os.environ.get("OPENROUTER_MODEL") or "nvidia/nemotron-3-super-120b-a12b:free"),
        )

    else:
        text = replace_line(text, "llm_provider", '"gemini"')
        text = replace_line(text, "gemini_api_key", json.dumps(required("GEMINI_API_KEY")))
        text = replace_line(
            text, "gemini_model_name",
            json.dumps(os.environ.get("GEMINI_MODEL") or "gemini-3.6-flash"),
        )

    return text


def main() -> None:
    mpt_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "mpt")
    example = mpt_dir / "config.example.toml"
    target = mpt_dir / "config.toml"
    if not example.exists():
        raise SystemExit(f"Missing {example}")

    shutil.copyfile(example, target)
    text = target.read_text(encoding="utf-8")

    pexels_key = required("PEXELS_API_KEY")

    # LLM provider block — gemini (default), groq, or openrouter.
    text = apply_llm_provider(text)

    # Video source.
    text = replace_line(text, "video_source", '"pexels"')
    text = replace_line(text, "pexels_api_keys", f"[{json.dumps(pexels_key)}]")
    text = replace_line(text, "subtitle_provider", '"edge"')

    # THE FOOTAGE FIX: tie each clip to the specific part of the script
    # playing at that moment. Off by default in MPT — we turn it on.
    match_materials = os.environ.get("MATCH_MATERIALS_TO_SCRIPT", "true").strip().lower()
    text = replace_line(
        text, "match_materials_to_script",
        "true" if match_materials not in ("0", "false", "no") else "false",
    )

    # Video layout.
    text = replace_commented_or_active_line(text, "video_aspect_pexels", '"9:16"')
    text = replace_commented_or_active_line(text, "video_count", "1")
    text = replace_commented_or_active_line(text, "video_fit_mode", '"cover"')

    # Voiceover / subtitle options — these come from [ui] and MPT's CLI
    # inherits them, so setting them in config.toml is enough.
    voice_name = os.environ.get("VOICE_NAME") or "en-US-GuyNeural"
    text = replace_commented_or_active_line(text, "voice_name", repr(voice_name))

    bgm_type = os.environ.get("BGM_TYPE") or "random"
    text = replace_commented_or_active_line(text, "bgm_type", repr(bgm_type))

    subtitle_position = os.environ.get("SUBTITLE_POSITION") or "bottom"
    text = replace_commented_or_active_line(text, "subtitle_position", repr(subtitle_position))

    subtitle_color = os.environ.get("SUBTITLE_COLOR") or "#FFFFFF"
    text = replace_commented_or_active_line(text, "text_fore_color", repr(subtitle_color))

    subtitle_bg_color = os.environ.get("SUBTITLE_BG_COLOR", "").strip()
    if subtitle_bg_color:
        text = replace_commented_or_active_line(text, "subtitle_background_color", repr(subtitle_bg_color))
        text = replace_commented_or_active_line(text, "subtitle_background_enabled", "true")

    target.write_text(text, encoding="utf-8")
    print(f"Prepared {target} (provider: {os.environ.get('LLM_PROVIDER', 'gemini')})")


if __name__ == "__main__":
    main()
