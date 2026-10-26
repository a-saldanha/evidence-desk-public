"""parse_date_text must handle written day-ranges ("13 - 17 September 2026", "17-18Sep2026").
dateutil's fuzzy parser otherwise reads the leading day as a year (2013/2017). Offline, no keys.

    python -m tests.test_temporal
"""
from __future__ import annotations
import sys
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from reason.temporal import parse_date_text

ANCHOR = datetime(2026, 7, 1)   # an email/message date in the case


def main():
    cases = [
        # date range -> start day, correct year (was 2013-09-17 / 2017-09-18 before the fix)
        ("13 - 17 September 2026", date(2026, 9, 13)),
        ("13-17 September 2026",   date(2026, 9, 13)),
        ("17-18Sep2026",          date(2026, 9, 17)),
        ("17–18 Sep 2026",        date(2026, 9, 17)),   # en-dash
        # single dates must be unaffected
        ("11Sep2026",             date(2026, 9, 11)),
        ("16-07-2026",            date(2026, 7, 16)),   # explicit day-first (16 > 12)
        ("Tue, 15 Sep 2026",      date(2026, 9, 15)),
    ]
    for text, expected in cases:
        d, how = parse_date_text(text, ANCHOR, day_first=True)
        assert d == expected, f"{text!r} -> {d} (how={how}), expected {expected}"
        print(f"  ok: {text!r:26} -> {d} ({how})")

    # a numeric range with dashes but no month must NOT be mistaken for a day-range
    d, _ = parse_date_text("16-07-2026", ANCHOR, day_first=True)
    assert d == date(2026, 7, 16)
    print("OK")


if __name__ == "__main__":
    main()
