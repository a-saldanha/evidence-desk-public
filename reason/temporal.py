"""Turns the LLM's description of *when* into actual dates, deterministically.

The LLM reports what the text says ("yesterday", "on Saturday", "16-07-2026", "9am"), never a computed date.
This module does the arithmetic against the unit's own timestamp, so "yesterday" in a message sent
17 Sep 21:52 always resolves to 16 Sep, and the result says how it was obtained.
"""
from __future__ import annotations
import re
from datetime import datetime, timedelta, date, time
from typing import Optional
from dateutil import parser as dtparser

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

WHEN_SCHEMA = {
    "type": "object",
    "description": "Describe what the text says about time. Do NOT compute dates yourself.",
    "properties": {
        "text": {"type": "string", "description": "the exact time phrase(s) from the text, or ''"},
        "date_text": {"type": ["string", "null"], "description": "an explicit calendar date exactly as written, e.g. '16-07-2026', '11Sep2026', 'Tue, 15 Sep 2026'"},
        "relative_day": {"type": ["integer", "null"], "description": "offset in days from when the message was written: today=0, yesterday=-1, tomorrow=1"},
        "weekday": {"type": ["string", "null"], "description": "weekday name if the text uses one ('on Saturday')"},
        "weekday_direction": {"anyOf": [{"type": "string", "enum": ["next", "last", "this"]}, {"type": "null"}],
                              "description": "direction for a weekday reference ('next'/'last'/'this'), or null"},
        "start_time": {"type": ["string", "null"], "description": "HH:MM 24h if stated"},
        "end_time": {"type": ["string", "null"], "description": "HH:MM 24h if stated"},
        "all_day": {"type": "boolean", "description": "true for states/events that cover the whole day (e.g. 'stayed in bed all day')"},
    },
    "required": ["text", "date_text", "relative_day", "weekday", "start_time", "end_time", "all_day"],
}


def _hm(s: Optional[str]) -> Optional[time]:
    if not s:
        return None
    m = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*$", s.strip(), re.I)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3):
        h = h % 12 + (12 if m.group(3).lower().startswith("p") else 0)
    return time(h % 24, mi) if mi < 60 else None


def parse_date_text(text: str, anchor: Optional[datetime], day_first: Optional[bool]) -> tuple[Optional[date], str]:
    t = text.strip()
    # A day range ("13 - 17 September 2026", "17-18Sep2026") -> its start day. Otherwise dateutil's fuzzy
    # parser reads the leading day as a year (2013 / 2017). Only fires when a month name follows the range.
    rng = re.match(r"^(\d{1,2})\s*(?:[-–—]|to)\s*\d{1,2}\s*([A-Za-z].*)$", t)
    if rng:
        t = f"{rng.group(1)} {rng.group(2)}"
    m = re.fullmatch(r"(\d{1,2})(\s?)([A-Za-z]{3,9})(\s?)(\d{4})", t)          # 11Sep2026
    if m:
        t = f"{m.group(1)} {m.group(3)} {m.group(5)}"
    numeric = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", t)
    try:
        default = (anchor or datetime(2000, 1, 1)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        if numeric:
            a, b = int(numeric.group(1)), int(numeric.group(2))
            if a > 12:
                df, how = True, "explicit"
            elif b > 12:
                df, how = False, "explicit"
            elif day_first is not None:
                df, how = day_first, "source_convention"
            else:
                return None, "ambiguous_day_month"
            return dtparser.parse(t, dayfirst=df, default=default).date(), how
        d = dtparser.parse(t, fuzzy=True, default=default).date()
        has_year = bool(re.search(r"\b\d{4}\b|'\d{2}\b", t))
        return d, "explicit" if has_year else "year_from_anchor"
    except (ValueError, OverflowError):
        return None, "unparseable"


def resolve(when: dict, anchor: Optional[datetime], day_first: Optional[bool] = None) -> dict:
    """anchor: naive local datetime of the unit that contains the expression (message time, email date)."""
    out = {"start_local": None, "end_local": None, "time_confidence": "none", "how": None}
    if not when:
        return out
    d, how = None, None
    if when.get("date_text"):
        d, how = parse_date_text(when["date_text"], anchor, day_first)
    if d is None and when.get("relative_day") is not None and anchor:
        d, how = anchor.date() + timedelta(days=int(when["relative_day"])), "relative_to_anchor"
    if d is None and when.get("weekday") and anchor and when["weekday"].lower() in WEEKDAYS:
        target = WEEKDAYS.index(when["weekday"].lower())
        delta = (target - anchor.weekday()) % 7
        direction = when.get("weekday_direction") or "next"
        if direction == "last":
            delta = delta - 7 if delta > 0 else -7
        # "next"/"this" with delta 0 means today ("on Saturday" written on a Saturday)
        d, how = anchor.date() + timedelta(days=delta), "weekday_from_anchor"
    if d is None and (when.get("start_time") or when.get("all_day")) and anchor and not when.get("date_text"):
        d, how = anchor.date(), "same_day_as_anchor"
    if d is None:
        out["how"] = how or "no_date"
        return out

    st, et = _hm(when.get("start_time")), _hm(when.get("end_time"))
    if when.get("all_day") or not st:
        start, end = datetime.combine(d, time(0, 0)), datetime.combine(d, time(23, 59))
        conf = "date_only"
    else:
        start = datetime.combine(d, st)
        end = datetime.combine(d, et) if et else None
        if end and end < start:
            end += timedelta(days=1)          # crosses midnight
        conf = "exact"
    if how in ("relative_to_anchor", "weekday_from_anchor", "same_day_as_anchor"):
        conf = "resolved_relative"
    if how == "year_from_anchor":
        conf = "date_only" if conf == "date_only" else "resolved_relative"
    out.update(start_local=start.isoformat(), end_local=end.isoformat() if end else None, time_confidence=conf, how=how)
    return out


def overlaps(a_start, a_end, b_start, b_end, slack_hours: float = 0) -> bool:
    if not a_start or not b_start:
        return False
    fa = lambda s: datetime.fromisoformat(s)
    as_, bs = fa(a_start), fa(b_start)
    ae = fa(a_end) if a_end else as_ + timedelta(hours=1)
    be = fa(b_end) if b_end else bs + timedelta(hours=1)
    s = timedelta(hours=slack_hours)
    return as_ - s < be and bs - s < ae          # touching at a boundary (midnight) is not an overlap
