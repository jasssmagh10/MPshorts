from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Callable

import requests


# Candidate order is tuned for short-form factual content and follows current, free-tier-capable IDs.
# The configured/preflight-selected model is attempted first; other models are fallback candidates.
PROVIDER_MODELS = {
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL", "gemini-3.8-flash", [
        "gemini-3.8-flash", "gemini-3.6-flash",
        "gemma-4-31b-it", "gemma-4-26b-a4b-it",
        "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3-flash-preview",
        "gemini-flash-latest", "gemini-flash-lite-latest",
        "gemini-2.5-flash", "gemini-2.5-flash-lite",
    ]),
    "groq": ("GROQ_API_KEY", "GROQ_MODEL", "qwen/qwen3.8-27b", [
        "qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "allam-2-7b",
    ]),
    "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "nvidia/nemotron-3.5-super-vl:free", [
        "nvidia/nemotron-3.5-super-vl:free",
        "google/gemma-4-31b-it:free",
        "google/gemma-4-26b-a4b-it:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3.5-lightning:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "dots-studio/dots-3-note-preview:free",
        "thinkingmachines/inkling-large:free",
        "thinkingmachines/inkling-small:free",
        "poolside/laguna-xs-2.1:free",
        "liquid/lfm-2.5-2.6b:free",
        "apodex/apodex-1-1-mini:free",
        "cohere/north-mini-code:free",
        "poolside/laguna-s-2.1:free",  # OpenRouter announced the free listing ends Oct 31, 2026.
    ]),
}
PROVIDER_ORDER = ("gemini", "groq", "openrouter")
PERMANENT_HTTP = {400, 401, 403, 404, 422}
MAX_MODELS_PER_PROVIDER = max(1, int(os.getenv("YTDESC_MAX_MODELS_PER_PROVIDER", "20")))
RETRY_ATTEMPTS = max(1, int(os.getenv("YTDESC_RETRIES", "2")))


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def model_candidates(provider: str) -> list[str]:
    key_env, model_env, default, fallbacks = PROVIDER_MODELS[provider]
    configured = os.environ.get(model_env, "").strip() or default
    candidates: list[str] = []
    for model in [configured, *fallbacks]:
        if model and model not in candidates:
            candidates.append(model)
    return candidates[:MAX_MODELS_PER_PROVIDER]


def call_gemini(prompt: str, model: str) -> str:
    api_key = required("GEMINI_API_KEY")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 800},
        },
        timeout=60,
    )
    response.raise_for_status()
    body = response.json()
    try:
        return body["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Gemini returned no text candidate") from exc


def call_openai_compatible(prompt: str, *, base_url: str, api_key: str, model: str) -> str:
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": 800,
        },
        timeout=60,
    )
    response.raise_for_status()
    body = response.json()
    try:
        content = body["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty response content")
        return content.strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("provider returned no text candidate") from exc


def call_provider_model(provider: str, prompt: str, model: str) -> str:
    if provider == "gemini":
        return call_gemini(prompt, model)
    if provider == "groq":
        return call_openai_compatible(
            prompt, base_url="https://api.groq.com/openai/v1",
            api_key=required("GROQ_API_KEY"), model=model,
        )
    if provider == "openrouter":
        if not model.endswith(":free"):
            raise ValueError("refusing non-free OpenRouter fallback model")
        return call_openai_compatible(
            prompt, base_url="https://openrouter.ai/api/v1",
            api_key=required("OPENROUTER_API_KEY"), model=model,
        )
    raise ValueError(f"unsupported provider: {provider}")


def retry_call(fn: Callable[[], str], *, label: str, attempts: int = RETRY_ATTEMPTS) -> str:
    """Retry only temporary API/network errors; never log response URLs or API keys."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            print(f"[{label}] attempt {attempt}/{attempts}", file=sys.stderr)
            return fn()
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in PERMANENT_HTTP or not (status in (408, 425, 429) or status >= 500):
                print(f"[{label}] non-retryable HTTP {status}; moving to fallback", file=sys.stderr)
                raise
            last = exc
            print(f"[{label}] temporary HTTP {status}", file=sys.stderr)
            if attempt >= attempts:
                break
            retry_after = exc.response.headers.get("Retry-After", "") if exc.response is not None else ""
            try:
                delay = min(15.0, max(1.0, float(retry_after)))
            except (TypeError, ValueError):
                delay = min(5.0, float(attempt * 2))
            time.sleep(delay)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError) as exc:
            last = exc
            print(f"[{label}] temporary network error: {type(exc).__name__}", file=sys.stderr)
            if attempt >= attempts:
                break
            time.sleep(min(5.0, float(attempt * 2)))
        except requests.exceptions.RequestException as exc:
            last = exc
            print(f"[{label}] request error: {type(exc).__name__}", file=sys.stderr)
            if attempt >= attempts:
                break
            time.sleep(min(5.0, float(attempt * 2)))
    if last:
        raise last
    raise RuntimeError(f"{label} failed without an error detail")


def normalize_hashtags(text: str) -> str:
    cleaned = re.sub(r"```(?:text)?|```", "", text, flags=re.IGNORECASE).strip()
    hashtags = re.findall(r"#[A-Za-z0-9]+", cleaned)
    hashtags = list(dict.fromkeys(hashtags))[:10]
    if len(hashtags) < 3:
        raise ValueError("model returned fewer than 3 valid hashtags")
    return " ".join(hashtags)


def call_llm(prompt: str) -> str:
    preferred = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()
    order = ([preferred] if preferred in PROVIDER_MODELS else []) + [
        provider for provider in PROVIDER_ORDER if provider != preferred
    ]
    errors: list[str] = []
    for provider in order:
        key_env = PROVIDER_MODELS[provider][0]
        if not os.environ.get(key_env, "").strip():
            print(f"[{provider}] skipped: missing {key_env}", file=sys.stderr)
            errors.append(f"{provider}: missing key")
            continue
        candidates = model_candidates(provider)
        provider_key_invalid = False
        for index, model in enumerate(candidates, start=1):
            label = f"youtube-description:{provider}:{model}"
            print(f"[{provider}] model {index}/{len(candidates)}: {model}", file=sys.stderr)
            try:
                raw = retry_call(lambda p=provider, m=model: call_provider_model(p, prompt, m), label=label)
                return normalize_hashtags(raw)
            except Exception as exc:
                status = exc.response.status_code if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None else None
                print(f"[{provider}] model failed ({type(exc).__name__}{f' HTTP {status}' if status else ''}); trying next fallback", file=sys.stderr)
                errors.append(f"{provider}/{model}: {type(exc).__name__}" + (f" HTTP {status}" if status else ""))
                if status == 401:
                    provider_key_invalid = True
                    print(f"[{provider}] authentication failed; skipping remaining models for this key", file=sys.stderr)
                    break
        if provider_key_invalid:
            continue
    raise RuntimeError("all YouTube-description model fallbacks failed: " + "; ".join(errors))


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python generate_youtube_description.py script.json output.txt")

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
    output_path.write_text(description + "\n", encoding="utf-8")
    print(description)


if __name__ == "__main__":
    main()
