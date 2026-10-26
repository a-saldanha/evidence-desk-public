"""Time-grid itineraries (rows = time slots, columns = dates) -> planned events. No LLM involved.

Works on any rectangular table of strings, so the same code handles
  * an .xlsx file (exact text, fill colours, no OCR),
  * a markdown table that LlamaParse produced from a screenshot,
  * a CSV export.
Consecutive identical cells in a date column become one event. TIME GAIN / TIME LOST cells are timezone
padding, not events: they are recorded as clock shifts. A '?' in a cell marks the event as tentative.
"""
from __future__ import annotations
import re
from datetime import datetime, timedelta
from typing import Optional
from ingest.models import stable_id
from .store import Event, Evidence

SLOT = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})\s*$")
DATE_HDR = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")
# "Dhanunjay Sir Presentation", "Reshu Ma'am Presentation", "Dr. Rao talk": a name before/after an honorific
PERSON_HONORIFIC = re.compile(r"^(?P<name>[A-Z][\w.]+(?: [A-Z][\w.]+)?)\s+(?:sir|ma'?am|madam|ji)\b|\b(?:dr|prof)\.?\s+(?P<name2>[A-Z][\w]+)", re.I)
CLOCK_SHIFT = re.compile(r"^\s*time\s+(gain|gained|lost|loss)\s*$", re.I)

KIND_RULES = [  # cheap categorisation; colours from xlsx override when present
    (re.compile(r"\b(to|flight|layover|bus|train|ferry|airport|luggage|board)\b", re.I), "travel"),
    (re.compile(r"\b(airbnb|hotel|check-?in|check-?out)\b", re.I), "accommodation"),
    (re.compile(r"\b(presentation|plenary|keynote|session|poster|talk)\b", re.I), "presentation"),
    (re.compile(r"\b(lunch|coffee|dinner|breakfast|gala|reception|networking|job fair|mentoring)\b", re.I), "social"),
]


def _html_tables(md: str) -> list[list[list[str]]]:
    """LlamaParse renders spreadsheet-like screenshots as HTML <table> blocks, not pipe tables."""
    if "<table" not in md.lower():
        return []
    from bs4 import BeautifulSoup
    out: list[list[list[str]]] = []
    for tbl in BeautifulSoup(md, "html.parser").find_all("table"):
        rows = []
        for tr in tbl.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        if rows:
            out.append(rows)
    return out


def markdown_tables(md: str) -> list[list[list[str]]]:
    """Tables from a parsed page: pipe-style markdown and HTML <table> (LlamaParse uses both)."""
    tables, cur = _html_tables(md), []
    for line in md.splitlines():
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            cur.append(cells)
        elif cur:
            tables.append(cur); cur = []
    if cur:
        tables.append(cur)
    return tables


def xlsx_table(path: str, sheet: Optional[str] = None) -> tuple[list[list[str]], dict]:
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet] if sheet else wb.active
    rows, colours = [], {}
    for r in ws.iter_rows():
        row = []
        for c in r:
            v = c.value
            row.append("" if v is None else (v.strftime("%d/%m/%y") if hasattr(v, "strftime") else str(v)))
            fill = getattr(c.fill, "fgColor", None)
            if fill is not None and getattr(fill, "rgb", None) and str(fill.rgb) not in ("00000000", "FFFFFFFF"):
                colours[(c.row - 1, c.column - 1)] = str(fill.rgb)
        rows.append(row)
    for rng in ws.merged_cells.ranges:          # spread merged values so runs are visible
        v = rows[rng.min_row - 1][rng.min_col - 1]
        for rr in range(rng.min_row - 1, rng.max_row):
            for cc in range(rng.min_col - 1, rng.max_col):
                rows[rr][cc] = v
    return rows, colours


def is_time_grid(rows: list[list[str]]) -> bool:
    slots = sum(1 for r in rows if r and SLOT.match(r[0] or ""))
    dates = max((sum(1 for c in r if DATE_HDR.search(c or "")) for r in rows[:5]), default=0)
    return slots >= 6 and dates >= 2


def _col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def grid_to_events(rows: list[list[str]], unit_id: str, colours: Optional[dict] = None,
                   day_first: Optional[bool] = True) -> tuple[list[Event], list[dict]]:
    """Returns (events, clock_shifts). Event evidence quotes the cell label and notes the cell range."""
    hdr_i = max(range(min(5, len(rows))), key=lambda i: sum(1 for c in rows[i] if DATE_HDR.search(c or "")))
    dates: dict[int, datetime] = {}
    for j, c in enumerate(rows[hdr_i]):
        m = DATE_HDR.search(c or "")
        if m:
            a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            y = y + 2000 if y < 100 else y
            d, mth = (a, b) if day_first is not False else (b, a)
            try:
                dates[j] = datetime(y, mth, d)
            except ValueError:
                continue
    slot_rows = [(i, SLOT.match(r[0])) for i, r in enumerate(rows) if r and SLOT.match(r[0] or "")]
    events, shifts = [], []
    for j, day in dates.items():
        run_label, run_start, run_rows = None, None, []
        def flush():
            if not run_label:
                return
            first_i, first_m = run_rows[0]
            last_i, last_m = run_rows[-1]
            start = day + timedelta(hours=int(first_m.group(1)), minutes=int(first_m.group(2)))
            end = day + timedelta(hours=int(last_m.group(3)), minutes=int(last_m.group(4)))
            if end <= start:
                end += timedelta(days=1)
            cell_range = f"{_col_letter(j)}{first_i + 1}:{_col_letter(j)}{last_i + 1}"
            label = run_label.strip()
            if CLOCK_SHIFT.match(label):
                shifts.append({"date": day.date().isoformat(), "kind": CLOCK_SHIFT.match(label).group(1).lower(),
                               "start_local": start.isoformat(), "hours": (end - start).total_seconds() / 3600,
                               "cells": cell_range})
                return
            kind = next((k for rx, k in KIND_RULES if rx.search(label)), "other")
            events.append(Event(
                event_id=stable_id(unit_id, "grid", cell_range), kind=kind, title=label.rstrip("?").strip(),
                status="planned", start_local=start.isoformat(), end_local=end.isoformat(),
                time_confidence="exact", tentative="?" in label, extracted_by="grid",
                participants=["self"] + [n for n in [(lambda m: m and (m.group("name") or m.group("name2")))(PERSON_HONORIFIC.search(label))] if n],
                evidence=[Evidence(unit_id=unit_id, quote=label, match="exact", note=f"cells {cell_range}")],
                meta={"cell_range": cell_range, "slots": len(run_rows),
                      "colour": (colours or {}).get((first_i, j))}))
        for i, m in slot_rows:
            val = (rows[i][j] if j < len(rows[i]) else "").strip()
            if val and val == run_label and run_rows and i == run_rows[-1][0] + 1:
                run_rows.append((i, m))
                continue
            flush()
            run_label, run_rows = (val, [(i, m)]) if val else (None, [])
        flush()
    # 1) clock-shift pieces that touch (22:00-24:00 + 00:00-01:30) are one shift
    shifts.sort(key=lambda x: x["start_local"])
    joined: list[dict] = []
    for sh in shifts:
        end = (datetime.fromisoformat(sh["start_local"]) + timedelta(hours=sh["hours"])).isoformat()
        if joined and joined[-1]["kind"] == sh["kind"] and joined[-1]["end_local"] == sh["start_local"]:
            joined[-1]["hours"] += sh["hours"]; joined[-1]["end_local"] = end; joined[-1]["cells"] += f", {sh['cells']}"
        else:
            joined.append(dict(sh, end_local=end))
    # 2) same label continuing across midnight, or interrupted only by a clock shift, is one event
    events.sort(key=lambda e: e.start_local)
    merged: list[Event] = []
    for e in events:
        prev = next((p for p in reversed(merged) if p.title == e.title), None)
        bridge = None
        if prev and prev.end_local != e.start_local:
            bridge = next((sh for sh in joined if sh["start_local"] == prev.end_local and sh["end_local"] == e.start_local), None)
        if prev and (prev.end_local == e.start_local or bridge):
            prev.end_local = e.end_local
            prev.evidence += e.evidence
            prev.meta["cell_range"] += f", {e.meta['cell_range']}"
            if bridge:
                prev.meta["crosses_timezone"] = {"kind": bridge["kind"], "hours": bridge["hours"]}
                prev.meta["note"] = "start and end are local times in different timezones; the grid pads the jump"
        else:
            merged.append(e)
    return merged, joined
