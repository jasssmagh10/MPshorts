from __future__ import annotations

import sys
from pathlib import Path

USED_FILE = Path("used_topics.txt")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python mark_topic_used.py <topic>")

    topic = sys.argv[1].strip()
    if not topic:
        raise SystemExit("No topic provided to mark as used")

    existing: set[str] = set()
    if USED_FILE.exists():
        existing = {
            line.strip()
            for line in USED_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    if topic in existing:
        print(f"Already marked as used: {topic}")
        return

    with USED_FILE.open("a", encoding="utf-8") as handle:
        handle.write(topic + "\n")
    print(f"Marked as used: {topic}")


if __name__ == "__main__":
    main()
