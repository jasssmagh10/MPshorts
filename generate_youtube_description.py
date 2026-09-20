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
    model = os.environ.get("GEMINI_MODEL") or "gemini-3.6-flash"
    api_key = required("GEMINI_API_KEY")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.4,
                # High enough to absorb any invisible "thinking" tokens the
                # model spends before writing the visible answer, without
                # relying on a thinkingConfig param this model rejects (400).
                "maxOutputTokens": 800,
            },
        },
        timeout=120,
    )
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise SystemExit(f"Gemini API error {response.status_code}: {response.text}") from exc
    body = response.json()
    try:
        return body["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise SystemExit(f"Gemini returned no description: {body}") from exc


def call_openai_compatible(prompt: str, *, base_url: str, api_key: str, model: str, label: str) -> str:
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": 800,
        },
        timeout=120,
    )
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise SystemExit(f"{label} API error {response.status_code}: {response.text}") from exc
    body = response.json()
    try:
        return body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise SystemExit(f"{label} returned no description: {body}") from exc


def call_llm(prompt: str) -> str:
    provider = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()

    if provider == "groq":
        return call_openai_compatible(
            prompt,
            base_url="https://api.groq.com/openai/v1",
            api_key=required("GROQ_API_KEY"),
            model=os.environ.get("GROQ_MODEL") or "openai/gpt-oss-120b",
            label="Groq",
        )

    if provider == "openrouter":
        return call_openai_compatible(
            prompt,
            base_url="https://openrouter.ai/api/v1",
            api_key=required("OPENROUTER_API_KEY"),
            model=os.environ.get("OPENROUTER_MODEL") or "nvidia/nemotron-3-super-120b-a12b:free",
            label="OpenRouter",
        )

    return call_gemini(prompt)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage: python generate_youtube_description.py script.json output.txt"
        )

    metadata_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    script = str(payload.get("script", "")).strip()
    terms = payload.get("search_terms", "")
    if isinstance(terms, list):
        terms = ", ".join(str(item) for item in terms)
    terms = str(terms).strip()

    if not script:
        raise SystemExit("MoneyPrinterTurbo metadata contains no generated script")

    prompt = f"""Create a compact hashtag-only YouTube Shorts description from the material below.

Rules:
- Return ONLY 5 to 10 relevant hashtags separated by single spaces.
- Do not write any normal sentence or explanatory text.
- Every hashtag must use only letters and numbers after #, with no spaces inside it.
- Do not invent specific facts that are not supported by the script.
- Do not include semicolons because the result will be placed in a semicolon-separated Telegram command.
- Keep the entire result under 250 characters.

Generated narration:
{script}

Pexels search terms:
{terms}
"""

    description = call_llm(prompt)

    description = re.sub(r"```(?:text)?|```", "", description, flags=re.IGNORECASE).strip()
    hashtags = re.findall(r"#[A-Za-z0-9]+", description)
    hashtags = list(dict.fromkeys(hashtags))[:10]
    if len(hashtags) < 3:
        raise SystemExit(f"LLM returned too few hashtags: {description}")
    description = " ".join(hashtags)

    output_path.write_text(description + "\n", encoding="utf-8")
    print(description)


if __name__ == "__main__":
    main()
