from __future__ import annotations

import os
import sys

import requests


def check_gemini() -> bool:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return False
    model = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            params={"key": key},
            json={"contents": [{"parts": [{"text": "hi"}]}],
                  "generationConfig": {"maxOutputTokens": 5}},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"  gemini: {e}", file=sys.stderr)
        return False


def check_groq() -> bool:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        return False
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "openai/gpt-oss-120b",
                  "messages": [{"role": "user", "content": "hi"}],
                  "max_tokens": 5},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"  groq: {e}", file=sys.stderr)
        return False


def check_openrouter() -> bool:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return False
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "nvidia/nemotron-3-super-120b-a12b:free",
                  "messages": [{"role": "user", "content": "hi"}],
                  "max_tokens": 5},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"  openrouter: {e}", file=sys.stderr)
        return False


def main() -> None:
    checks = [
        ("gemini", check_gemini),
        ("groq", check_groq),
        ("openrouter", check_openrouter),
    ]
    healthy = []
    for name, fn in checks:
        ok = fn()
        print(f"{name}: {'OK' if ok else 'FAILED'}", file=sys.stderr)
        if ok:
            healthy.append(name)

    print(f"\nHealthy providers: {', '.join(healthy) or 'NONE'}")
    if len(healthy) < 2:
        raise SystemExit(f"Need at least 2 healthy providers, got {len(healthy)}")


if __name__ == "__main__":
    main()