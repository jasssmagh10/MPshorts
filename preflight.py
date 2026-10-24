"""Check LLM providers and select a working model for this workflow run.

The configured model is always tried first. If it is unavailable, the script
uses provider model catalogs plus conservative fallbacks. It never prints API
keys or full response bodies (which may contain sensitive request metadata).
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

TIMEOUT = (5, 18)
RETRIES = max(0, int(os.getenv("PREFLIGHT_RETRIES", "2")))
MAX_MODELS = max(1, int(os.getenv("PREFLIGHT_MAX_MODELS_PER_PROVIDER", "24")))
GAP_SECONDS = max(0.0, float(os.getenv("PREFLIGHT_REQUEST_GAP_SECONDS", "0.25")))

GEMINI_DEFAULT = "gemini-3.8-flash"
GROQ_DEFAULT = "qwen/qwen3.8-27b"
OPENROUTER_DEFAULT = "nvidia/nemotron-3.5-super-vl:free"

# First choices prioritize current free-tier text models for short factual scripts.
# Live catalog fallbacks are appended so model retirement does not permanently mark a provider down.
GEMINI_FALLBACKS = [
    # Current free-tier general text models first; skip paid Pro and specialist media models.
    "gemini-3.8-flash", "gemini-3.6-flash",
    "gemma-4-31b-it", "gemma-4-26b-a4b-it",
    "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3-flash-preview",
    "gemini-flash-latest", "gemini-flash-lite-latest",
    "gemini-2.5-flash", "gemini-2.5-flash-lite",
]
GROQ_FALLBACKS = [
    # Current free-plan general chat candidates; old Llama 3.3 is retired.
    "qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "allam-2-7b",
]
OPENROUTER_FALLBACKS = [
    # All are explicit free variants. General writing/reasoning first; specialized models last.
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
]

@dataclass
class ProbeResult:
    ok: bool
    category: str
    detail: str


def classify_status(status: int, message: str = "") -> str:
    """Return a safe, useful category; never include raw provider response text."""
    msg = message.lower()
    if status in (401,):
        return "AUTHENTICATION_FAILED (check API key/secret)"
    if status == 403:
        if any(word in msg for word in ("quota", "billing", "limit", "exhaust")):
            return "QUOTA_OR_PERMISSION (check quota, billing, and model access)"
        return "PERMISSION_DENIED (key lacks permission or model access)"
    if status == 404:
        return "MODEL_OR_ENDPOINT_NOT_FOUND (model may be retired or unavailable)"
    if status == 429:
        return "RATE_LIMITED_OR_QUOTA_EXHAUSTED"
    if status in (408, 425):
        return "TEMPORARY_TIMEOUT_OR_OVERLOAD"
    if status >= 500:
        return "PROVIDER_SERVER_ERROR"
    if status in (400, 422):
        if any(word in msg for word in ("model", "not found", "unsupported", "does not exist")):
            return "MODEL_NOT_AVAILABLE_OR_UNSUPPORTED"
        if any(word in msg for word in ("quota", "billing", "limit")):
            return "QUOTA_OR_LIMIT"
        return "REQUEST_REJECTED (check provider/model compatibility)"
    if 200 <= status < 300:
        return "OK"
    return f"HTTP_{status}"


def _safe_error(response: requests.Response) -> str:
    # Provider error messages are useful for classifying model/quota problems,
    # but only a short, sanitized excerpt is used internally and never printed verbatim.
    try:
        body = response.json()
        if isinstance(body, dict):
            err = body.get("error", body)
            if isinstance(err, dict):
                return " ".join(str(err.get(k, "")) for k in ("message", "status", "code"))[:500]
            return str(err)[:500]
    except (ValueError, requests.RequestException, AttributeError):
        pass
    return ""


def request_with_retry(method: str, url: str, *, provider: str, **kwargs: Any) -> tuple[requests.Response | None, str]:
    """Retry network and temporary server failures; respect Retry-After safely."""
    last_detail = "network error"
    for attempt in range(RETRIES + 1):
        try:
            response = requests.request(method, url, timeout=TIMEOUT, **kwargs)
            if response.status_code < 500 and response.status_code not in (408, 425, 429):
                return response, ""
            last_detail = classify_status(response.status_code, _safe_error(response))
            if attempt >= RETRIES:
                return response, last_detail
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = min(8.0, max(0.0, float(retry_after)))
            except ValueError:
                delay = min(4.0, 0.5 * (2 ** attempt)) + random.uniform(0, 0.2)
            time.sleep(delay)
        except requests.RequestException as exc:
            last_detail = f"NETWORK_ERROR ({type(exc).__name__})"
            if attempt >= RETRIES:
                return None, last_detail
            time.sleep(min(4.0, 0.5 * (2 ** attempt)) + random.uniform(0, 0.2))
    return None, last_detail


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for item in items:
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _gemini_catalog(key: str) -> list[str]:
    response, _ = request_with_retry(
        "GET", "https://generativelanguage.googleapis.com/v1beta/models",
        provider="gemini", params={"key": key, "pageSize": 1000},
    )
    if not response or response.status_code != 200:
        return []
    try:
        data = response.json().get("models", [])
        names = []
        for item in data:
            name = str(item.get("name", "")).removeprefix("models/")
            methods = item.get("supportedGenerationMethods", [])
            # Only probe free-tier general text models suitable for script writing.
            # Current Flash models and Gemma 4 are included; specialist media models are excluded.
            lower = name.lower()
            excluded = ("-tts", "-live", "image", "transcribe", "embedding", "native-audio", "pro-preview")
            is_general_text_model = "flash" in lower or lower.startswith("gemma-4-")
            if name and "generateContent" in methods and is_general_text_model and not any(x in lower for x in excluded):
                names.append(name)
        preferred = {name: i for i, name in enumerate(GEMINI_FALLBACKS)}
        return sorted(names, key=lambda x: (preferred.get(x, 999), "preview" in x.lower(), x))
    except (ValueError, AttributeError, TypeError):
        return []


def _openai_compatible_catalog(provider: str, key: str, base_url: str) -> list[str]:
    headers = {"Authorization": f"Bearer {key}"}
    response, _ = request_with_retry("GET", f"{base_url}/models", provider=provider, headers=headers)
    if not response or response.status_code != 200:
        return []
    try:
        entries = response.json().get("data", [])
        if provider == "openrouter":
            names = []
            for item in entries:
                name = str(item.get("id", ""))
                if not name.endswith(":free"):
                    continue  # Never silently route this workflow to a paid model.
                lower = name.lower()
                if any(x in lower for x in ("content-safety", "prompt-guard", "moderation", "embedding", "-tts")):
                    continue
                architecture = item.get("architecture") or {}
                outputs = architecture.get("output_modalities") or []
                if outputs and "text" not in outputs:
                    continue
                names.append(name)
            preferred = {name: i for i, name in enumerate(OPENROUTER_FALLBACKS)}
            return sorted(names, key=lambda x: (preferred.get(x, 999), x))
        names = [str(item.get("id", "")) for item in entries if item.get("id")]
        if provider == "groq":
            # Catalog may include models that cannot generate chat completions.
            names = [name for name in names if not any(x in name.lower() for x in ("whisper", "guard", "safeguard", "embedding", "speech", "transcrib", "orpheus"))]
            preferred = {name: i for i, name in enumerate(GROQ_FALLBACKS)}
            return sorted(names, key=lambda x: (preferred.get(x, 999), x))
        return names
    except (ValueError, AttributeError, TypeError):
        return []


def _probe_gemini(key: str, model: str) -> ProbeResult:
    response, network_detail = request_with_retry(
        "POST", f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        provider="gemini", params={"key": key},
        json={"contents": [{"parts": [{"text": "Reply with OK."}]}],
              "generationConfig": {"maxOutputTokens": 2, "temperature": 0}},
    )
    if response is None:
        return ProbeResult(False, network_detail, "request did not reach provider")
    if response.status_code == 200:
        return ProbeResult(True, "OK", "minimal generation succeeded")
    return ProbeResult(False, classify_status(response.status_code, _safe_error(response)), "model probe rejected")


def _probe_chat(provider: str, key: str, model: str, base_url: str) -> ProbeResult:
    response, network_detail = request_with_retry(
        "POST", f"{base_url}/chat/completions", provider=provider,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": "Reply with OK."}],
              "max_tokens": 2, "temperature": 0},
    )
    if response is None:
        return ProbeResult(False, network_detail, "request did not reach provider")
    if response.status_code == 200:
        return ProbeResult(True, "OK", "minimal generation succeeded")
    return ProbeResult(False, classify_status(response.status_code, _safe_error(response)), "model probe rejected")


def check_provider(name: str, key_env: str, model_env: str, default_model: str,
                   fallbacks: list[str], catalog_fn: Callable[[str], list[str]],
                   probe_fn: Callable[[str, str], ProbeResult]) -> tuple[bool, str, str]:
    key = os.environ.get(key_env, "").strip()
    configured = os.environ.get(model_env, "").strip() or default_model
    if not key:
        print(f"[{name}] FAIL: MISSING_API_KEY ({key_env} is not set)", file=sys.stderr)
        return False, "", "MISSING_API_KEY"

    # Try configured model first, then known fallbacks, then live catalog models.
    catalog = catalog_fn(key)
    models = _unique([configured, *fallbacks, *catalog])[:MAX_MODELS]
    failures: list[tuple[str, str]] = []
    for index, model in enumerate(models, start=1):
        result = probe_fn(key, model)
        if result.ok:
            print(f"[{name}] OK: model={model} (working model {index}/{len(models)})", file=sys.stderr)
            if model != configured:
                print(f"[{name}] NOTE: configured model '{configured}' failed; selected fallback '{model}'", file=sys.stderr)
            return True, model, "OK"
        failures.append((model, result.category))
        print(f"[{name}] model={model}: {result.category}", file=sys.stderr)
        # Invalid credentials affect every model; stop spending calls immediately.
        if result.category.startswith(("AUTHENTICATION_FAILED", "PERMISSION_DENIED")):
            break
        if GAP_SECONDS:
            time.sleep(GAP_SECONDS)

    last_category = failures[-1][1] if failures else "NO_CANDIDATE_MODELS"
    tried = ", ".join(f"{model} [{category}]" for model, category in failures)
    print(f"[{name}] FAIL: no working model found; tried {len(failures)} candidate(s). {tried}", file=sys.stderr)
    return False, "", last_category


def main() -> None:
    providers = [
        ("gemini", "GEMINI_API_KEY", "GEMINI_MODEL", GEMINI_DEFAULT, GEMINI_FALLBACKS,
         _gemini_catalog, lambda key, model: _probe_gemini(key, model)),
        ("groq", "GROQ_API_KEY", "GROQ_MODEL", GROQ_DEFAULT, GROQ_FALLBACKS,
         lambda key: _openai_compatible_catalog("groq", key, "https://api.groq.com/openai/v1"),
         lambda key, model: _probe_chat("groq", key, model, "https://api.groq.com/openai/v1")),
        ("openrouter", "OPENROUTER_API_KEY", "OPENROUTER_MODEL", OPENROUTER_DEFAULT, OPENROUTER_FALLBACKS,
         lambda key: _openai_compatible_catalog("openrouter", key, "https://openrouter.ai/api/v1"),
         lambda key, model: _probe_chat("openrouter", key, model, "https://openrouter.ai/api/v1")),
    ]
    healthy: list[str] = []
    selected: dict[str, str] = {}
    for name, key_env, model_env, default, fallbacks, catalog_fn, probe_fn in providers:
        ok, model, category = check_provider(name, key_env, model_env, default, fallbacks, catalog_fn, probe_fn)
        if ok:
            healthy.append(name)
            selected[model_env] = model

    print(f"\nHealthy providers: {', '.join(healthy) or 'NONE'}")
    print(f"Provider policy: need at least 2 healthy providers; found {len(healthy)}")

    # GitHub Actions step outputs let downstream steps use the model that actually passed.
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            for env_name, model in selected.items():
                output.write(f"{env_name.lower()}={model}\n")
            output.write(f"healthy_count={len(healthy)}\n")
            output.write(f"healthy_providers={','.join(healthy)}\n")

    if len(healthy) < 2:
        raise SystemExit(
            f"Preflight failed: need at least 2 healthy providers, got {len(healthy)}. "
            "Review the classified failures above; workflow stopped before expensive generation."
        )


if __name__ == "__main__":
    main()
