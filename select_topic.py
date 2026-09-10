from __future__ import annotations

from pathlib import Path

TOPICS_FILE = Path("topics.txt")
USED_FILE = Path("used_topics.txt")
SELECTED_FILE = Path("selected_topic.txt")


def load_topics(path: Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"{path} is missing")

    seen: set[str] = set()
    topics: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        topic = line.strip()
        if not topic or topic.startswith("#"):
            continue
        # Duplicate lines in topics.txt count as one topic; keep the first
        # occurrence's wording as the canonical title.
        if topic not in seen:
            seen.add(topic)
            topics.append(topic)

    if not topics:
        raise SystemExit(f"{path} contains no usable topics")
    return topics


def load_used(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def main() -> None:
    topics = load_topics(TOPICS_FILE)
    used = load_used(USED_FILE)

    if set(topics) <= used:
        # Every topic has already been used at least once — start a fresh
        # cycle instead of failing the scheduled run.
        print("All topics used once; starting a new cycle.")
        used = set()
        USED_FILE.write_text("", encoding="utf-8")

    for topic in topics:
        if topic not in used:
            SELECTED_FILE.write_text(topic + "\n", encoding="utf-8")
            print(topic)
            return

    raise SystemExit("No topic could be selected — this should not happen")


if __name__ == "__main__":
    main()
