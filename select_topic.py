from __future__ import annotations

import datetime as dt
from pathlib import Path


def main() -> None:
    topics = [
        line.strip()
        for line in Path("topics.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not topics:
        raise SystemExit("topics.txt is empty")

    today = dt.date.today()
    topic = topics[(today - dt.date(today.year, 1, 1)).days % len(topics)]
    Path("selected_topic.txt").write_text(topic + "\n", encoding="utf-8")
    print(topic)


if __name__ == "__main__":
    main()
