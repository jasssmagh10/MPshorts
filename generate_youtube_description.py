from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import requests


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def call_gemini(prompt: str) -> str:
    key = required("GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"temperature": 0.4, "maxOutputTokens": 800}},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def call_groq(prompt: str) -> str:
    key = required("GROQ_API_KEY")
    model = os.environ.get("GROQ_MODEL") or "openai/gpt-oss-120b"
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model,
              "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.4},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def call_openrouter(prompt: str) -> str:
    key = required("OPENROUTER_API_KEY")
    model = os.environ.get("OPENROUTER_MODEL") or "nvidia/nemotron-3-super-120b-a12b:free"
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model,
              "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.4},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


PROVIDERS = [("gemini", call_gemini), ("groq", call_groq), ("openrouter", call_openrouter)]


def build_prompt(script: str, terms: str) -> str:
    return f"""Create a compact hashtag-only YouTube Shorts description from the material below.

Rules:
- Return ONLY 5 to 10 relevant hashtags separated by single spaces.
- Do not write any normal sentence or explanatory text.
- Every hashtag must use only letters and numbers after #, no spaces inside it.
- Do not invent specific facts not supported by the script.
- Do not include semicolons.
- Keep the entire result under 250 characters.
- Do NOT include #Shorts.

Generated narration:
{script}

Pexels search terms:
{terms}
"""


def clean_hashtags(text: str) -> str:
    text = re.sub(r"```(?:text)?|```", "", text, flags=re.IGNORECASE).strip()
    tags = re.findall(r"#[A-Za-z0-9]+", text)
    tags = [t for t in dict.fromkeys(tags) if t.lower() != "#shorts"]
    return " ".join(tags[:10])


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage: python generate_youtube_description.py script.json output.txt"
        )

    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    script = str(payload.get("script", "")).strip()
    terms = payload.get("search_terms", "")
    if isinstance(terms, list):
        terms = ", ".join(str(item) for item in terms)
    terms = str(terms).strip()

    if not script:
        raise SystemExit("No script found in metadata")

    prompt = build_prompt(script, terms)

    last_error = None
    for name, fn in PROVIDERS:
        try:
            print(f"[description] trying {name}", file=sys.stderr)
            raw = fn(prompt)
            description = clean_hashtags(raw)
            if len(description.split()) >= 3:
                Path(sys.argv[2]).write_text(description + "\n", encoding="utf-8")
                print(f"[description] success with {name}", file=sys.stderr)
                print(description)
                return
            print(f"[description] {name} returned too few hashtags", file=sys.stderr)
        except Exception as exc:
            print(f"[description] {name} failed: {exc}", file=sys.stderr)
            last_error = exc

    raise SystemExit(f"All description providers failed. Last: {last_error}")


if __name__ == "__main__":
    main()