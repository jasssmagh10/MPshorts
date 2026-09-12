from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from pathlib import Path

import requests


MAX_TOPICS_PER_RUN = 3


# ---------- CTA pool ----------

CHANNEL_CTA_POOL = [
    "Follow for more.",
    "More coming next.",
    "Follow for more like this.",
    "More facts coming up.",
    "Follow so you don't miss the next one.",
    "More on the way.",
    "Follow for more facts.",
    "Next fact coming soon.",
    "Follow — more where this came from.",
    "More like this tomorrow.",
    "Follow for the next one.",
    "New fact tomorrow.",
    "Follow and stay curious.",
    "More weird truths soon.",
    "Follow for daily facts.",
    "Next one drops tomorrow.",
    "Follow for the stuff you didn't know.",
    "More soon.",
]


def pick_cta() -> str:
    return random.choice(CHANNEL_CTA_POOL)


# ---------- helpers ----------

def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


# ---------- provider calls ----------

def call_gemini(prompt: str, temperature: float) -> str:
    key = required("GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"temperature": temperature, "maxOutputTokens": 1024}},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def call_groq(prompt: str, temperature: float) -> str:
    key = required("GROQ_API_KEY")
    model = os.environ.get("GROQ_MODEL") or "openai/gpt-oss-120b"
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model,
              "messages": [{"role": "user", "content": prompt}],
              "temperature": temperature},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def call_openrouter(prompt: str, temperature: float) -> str:
    key = required("OPENROUTER_API_KEY")
    model = os.environ.get("OPENROUTER_MODEL") or "nvidia/nemotron-3-super-120b-a12b:free"
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model,
              "messages": [{"role": "user", "content": prompt}],
              "temperature": temperature},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


PROVIDERS = [("gemini", call_gemini), ("groq", call_groq), ("openrouter", call_openrouter)]
PERMANENT_STATUS = {400, 401, 403, 404, 422}


def call_with_retry(fn, prompt: str, temperature: float, label: str, attempts: int = 3) -> str:
    last = None
    for i in range(1, attempts + 1):
        try:
            print(f"[{label}] attempt {i}/{attempts}", file=sys.stderr)
            return fn(prompt, temperature)
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in PERMANENT_STATUS:
                print(f"[{label}] permanent HTTP {status}, skip retry", file=sys.stderr)
                raise
            last = exc
            print(f"[{label}] attempt {i} failed HTTP {status}", file=sys.stderr)
        except Exception as exc:
            last = exc
            print(f"[{label}] attempt {i} failed: {exc}", file=sys.stderr)
        if i < attempts:
            time.sleep(15 * i)
    raise last or RuntimeError("no exception")


# ---------- prompts ----------

MPT_SCRIPT_RULES = """# Role: Video Script Generator

## Goals:
Generate a script for a video, depending on the subject of the video.

## Constrains:
1. the script is to be returned as a string with the specified number of paragraphs.
2. do not under any circumstance reference this prompt in your response.
3. get straight to the point, don't start with unnecessary things like, "welcome to this video".
4. you must not include any type of markdown or formatting in the script, never use a title.
5. only return the raw content of the script.
6. do not include "voiceover", "narrator" or similar indicators of what should be spoken at the beginning of each paragraph or line.
7. you must not mention the prompt, or anything about the script itself. also, never talk about the amount of paragraphs or lines. just write the script.
8. respond in the same language as the video subject.

## Format Selection:
Choose the ONE format below that best fits the topic, then write the script in that format.

- MYTH-BUSTER: Use only if the topic involves a common belief, myth, or assumption that is wrong or misleading.
  Structure: State the popular belief as if the viewer probably believes it → reveal the truth → explain why the myth spread.
  Example topic fit: "Marie Antoinette never said let them eat cake."

- QUESTION-ANSWER: Use only if the topic is naturally a "why" or "how" question, or can be framed as one.
  Structure: Pose the surprising question → explain the mechanism or cause → deliver the answer.
  Example topic fit: "Why phones lose signal in elevators."

- FACT-STACKER: Default. Use this for any topic that is a standalone surprising fact that doesn't fit the two above.
  Structure: Hook with the most surprising fact → stack two more related details → close with a takeaway.
  Example topic fit: "Napoleon was once attacked by a pack of rabbits."

Do NOT mix formats. Pick one and follow its structure cleanly.

## Output Format (IMPORTANT):
Your response must have EXACTLY two parts, in this order:

Line 1: FORMAT: <chosen format name, one of: MYTH-BUSTER, QUESTION-ANSWER, FACT-STACKER>
Lines 2+: The script text, nothing else. No labels, no markers, no blank lines at the start.

Example response:
FORMAT: FACT-STACKER
Napoleon was once attacked by a pack of rabbits during a hunt, and he lost. The rabbits reportedly swarmed him in 1807 while he was hunting near his troops. The event became one of the strangest military defeats in history. Follow for more.

## Additional Rules:
- Keep the script between 48 and 65 words, including the closing line. Never exceed 70 words.
- Only state facts that are verifiably true. If uncertain about a claim, omit it.
- Avoid absolute words like "only", "never", "always", "impossible" unless literally true.
- Do not invent names, dates, or statistics. If a specific number is needed, use a widely documented one.
- Hook the viewer in the first sentence with the most surprising specific fact.
- End the script with this exact closing line, copied verbatim with no changes and no additions: "{cta}"
- The closing line must be the final sentence. Do not add any subscribe request, call to action, or sign-off beyond it."""


def build_prompt(topic: str, cta: str) -> str:
    return f"""{MPT_SCRIPT_RULES}

# Initialization:
- video subject: {topic}
- number of paragraphs: 1
- required closing line: {cta}"""


def build_terms_prompt(topic: str) -> str:
    return f"""Generate 5 short search phrases for finding stock video footage about: {topic}

Rules:
- Each phrase: 2 to 4 words, plain English, suitable for a stock footage search.
- Return ONLY the phrases, comma-separated, on a single line.
- No numbering, no bullets, no explanation.

Output:"""


# ---------- sanitizers ----------

VALID_FORMATS = {"MYTH-BUSTER", "QUESTION-ANSWER", "FACT-STACKER"}


def parse_format_and_script(text: str) -> tuple[str, str]:
    """Extract FORMAT line from the LLM response. Returns (format_name, script)."""
    text = re.sub(r"```[a-zA-Z]*|```", "", text).strip()
    lines = text.splitlines()

    chosen_format = "FACT-STACKER"
    script_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        m = re.match(r"^FORMAT\s*:\s*(.+)$", stripped, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().upper().replace("_", "-")
            if candidate in VALID_FORMATS:
                chosen_format = candidate
            script_start = i + 1
        break

    script = "\n".join(lines[script_start:]).strip()
    return chosen_format, script


def clean_script(text: str) -> str:
    text = re.sub(r"```[a-zA-Z]*|```", "", text)
    text = text.replace("**", "").replace("*", "").replace("#", "")
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_terms(text: str) -> str:
    text = text.split("\n")[0].strip()
    text = re.sub(r"^[\d\.\-\)\s]+", "", text)
    parts = [p.strip().strip('"').strip("'") for p in text.split(",") if p.strip()]
    return ", ".join(parts[:6])


# ---------- topic selection ----------

def load_topics(path: Path) -> list[str]:
    seen, out = set(), []
    for line in path.read_text(encoding="utf-8").splitlines():
        t = line.strip()
        if not t or t.startswith("#") or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def load_used(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()}


def append_used(path: Path, topic: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(topic + "\n")


def pick_next_topic(topics: list[str], used: set[str]) -> str | None:
    for t in topics:
        if t not in used:
            return t
    return None


# ---------- generation ----------

def generate(topic: str) -> tuple[str, str, str]:
    """Returns (provider_name, format_name, script_text)."""
    cta = pick_cta()
    print(f"[cta] picked: {cta}", file=sys.stderr)
    prompt = build_prompt(topic, cta)
    errors = []
    for name, fn in PROVIDERS:
        try:
            raw = call_with_retry(fn, prompt, 0.8, f"gen-{name}")
            chosen_format, script = parse_format_and_script(raw)
            script = clean_script(script)
            if script:
                return name, chosen_format, script
            errors.append(f"{name}: empty")
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise RuntimeError("all generators failed: " + "; ".join(errors))


def generate_terms(topic: str) -> str:
    prompt = build_terms_prompt(topic)
    for name, fn in PROVIDERS:
        try:
            raw = call_with_retry(fn, prompt, 0.4, f"terms-{name}", attempts=2)
            terms = clean_terms(raw)
            if terms:
                return terms
        except Exception as exc:
            print(f"[terms-{name}] failed: {exc}", file=sys.stderr)
    return topic


# ---------- fact-check ----------

def check_with(provider_name: str, fn, script: str) -> tuple[bool, str]:
    prompt = f"""You are a strict fact-checker.

Read the script below. For every factual claim (people, dates, numbers, events, scientific statements), classify as TRUE, FALSE, or UNVERIFIABLE.

Also flag:
- Absolute words ("only", "never", "always", "impossible") used incorrectly
- Specific names, dates, or numbers that appear invented
- Claims that mix up two real things
- Claims that are plausible-sounding but not supported by mainstream sources
- Confusion between "casualty" (killed OR wounded) and "fatality" (killed only)
- Casualties or deaths attributed to the wrong side in a conflict
- Numbers that sound precise but are commonly miscited

If the script is factually sound, reply exactly: PASS
If ANY claim is false or suspicious, reply exactly:
FAIL: <one-line reason>

Script:
{script}"""
    try:
        result = call_with_retry(fn, prompt, 0.1, f"check-{provider_name}", attempts=2).strip()
        passed = result.upper().startswith("PASS")
        return passed, result
    except Exception as exc:
        return False, f"ERROR: {exc}"


def verify_script(script: str, exclude_provider: str) -> tuple[bool, str]:
    checkers = [(n, f) for n, f in PROVIDERS if n != exclude_provider]
    if not checkers:
        return False, "no independent checker available"

    verdicts = []
    for name, fn in checkers:
        passed, verdict = check_with(name, fn, script)
        verdicts.append(f"{name}: {verdict}")
        if passed:
            return True, f"PASS (checked by {name})"
        print(f"[check-{name}] FAIL: {verdict}", file=sys.stderr)

    return False, " | ".join(verdicts)


# ---------- main ----------

def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit(
            "Usage: python generate_script.py <topics.txt> <used_topics.txt> <script.json> <selected_topic.txt>"
        )

    topics_path = Path(sys.argv[1])
    used_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])
    selected_path = Path(sys.argv[4])

    topics = load_topics(topics_path)
    if not topics:
        raise SystemExit(f"No topics found in {topics_path}")

    used = load_used(used_path)
    tried_this_run = []

    for attempt in range(1, MAX_TOPICS_PER_RUN + 1):
        topic = pick_next_topic(topics, used)
        if topic is None:
            print("All topics exhausted, resetting used list", file=sys.stderr)
            used_path.write_text("", encoding="utf-8")
            used = set()
            topic = pick_next_topic(topics, used)
            if topic is None:
                raise SystemExit("No topics available after reset")

        tried_this_run.append(topic)
        print(f"\n=== Topic {attempt}/{MAX_TOPICS_PER_RUN}: {topic} ===", file=sys.stderr)

        try:
            gen_provider, format_name, script = generate(topic)
        except RuntimeError as exc:
            print(f"[topic {attempt}] generation failed: {exc}", file=sys.stderr)
            append_used(used_path, topic)
            used.add(topic)
            continue

        wc = len(script.split())
        print(f"[topic {attempt}] {gen_provider} wrote {wc} words in {format_name} format", file=sys.stderr)

        approved, verdict = verify_script(script, exclude_provider=gen_provider)
        print(f"[topic {attempt}] verdict: {verdict}", file=sys.stderr)

        if approved:
            terms = generate_terms(topic)
            output_path.write_text(
                json.dumps({
                    "script": script,
                    "search_terms": terms,
                    "generated_by": gen_provider,
                    "format": format_name,
                    "verified_by": verdict,
                    "word_count": wc,
                }, indent=2),
                encoding="utf-8",
            )
            selected_path.write_text(topic + "\n", encoding="utf-8")
            print(f"APPROVED: {topic} (by {gen_provider}, {format_name}, {wc} words)")
            return

        print(f"[topic {attempt}] REJECTED — abandoning topic, trying next", file=sys.stderr)
        append_used(used_path, topic)
        used.add(topic)

    raise SystemExit(f"All {MAX_TOPICS_PER_RUN} topics rejected. Tried: {tried_this_run}")


if __name__ == "__main__":
    main()