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

    # Use a fixed epoch (not Jan 1 of the current year) so the index keeps
    # advancing across the whole list instead of wrapping back to the same
    # ~366 topics every year via day-of-year % len(topics).
    epoch = dt.date(2020, 1, 1)
    today = dt.date.today()
    topic = topics[(today - epoch).days % len(topics)]
    Path("selected_topic.txt").write_text(topic + "\n", encoding="utf-8")
    print(topic)


if __name__ == "__main__":
    main()
