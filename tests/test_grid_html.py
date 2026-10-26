"""Grid parser must accept the HTML <table> shape real LlamaParse returns for a spreadsheet screenshot
(the test fixture_itinerary_grid.py uses pipe-markdown; the live API uses <table>). Offline, no keys.

    python -m tests.test_grid_html
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from reason.grid import markdown_tables, is_time_grid, grid_to_events

# A compact time grid as LlamaParse emits it: rows = time slots, columns = dates, cells may wrap <strong>.
HTML_GRID = """<table>
<tr><th>date</th><th>15/09/26</th><th>16/09/26</th></tr>
<tr><th>day</th><th>Monday</th><th>Tuesday</th></tr>
<tr><td><strong>13:00 – 13:30</strong></td><td></td><td></td></tr>
<tr><td><strong>13:30 – 14:00</strong></td><td></td><td></td></tr>
<tr><td><strong>14:00 – 14:30</strong></td><td></td><td>Dhanunjay Sir Presentation</td></tr>
<tr><td><strong>14:30 – 15:00</strong></td><td></td><td>Dhanunjay Sir Presentation</td></tr>
<tr><td><strong>15:00 – 15:30</strong></td><td>ESCAN Presentation</td><td></td></tr>
<tr><td><strong>15:30 – 16:00</strong></td><td></td><td>TIME LOST</td></tr>
</table>"""


def main():
    tables = markdown_tables(HTML_GRID)
    assert tables, "no table parsed from HTML"
    grid = next((t for t in tables if is_time_grid(t)), None)
    assert grid is not None, "HTML table not recognised as a time grid"
    events, shifts = grid_to_events(grid, "u_html")
    titles = {e.title for e in events}
    assert "Dhanunjay Sir Presentation" in titles, titles

    dp = next(e for e in events if e.title == "Dhanunjay Sir Presentation")
    assert dp.start_local == "2026-09-16T14:00:00", dp.start_local          # 16 Sep, merged 14:00-15:00
    assert dp.end_local == "2026-09-16T15:00:00", dp.end_local
    assert dp.status == "planned"
    assert "Dhanunjay" in dp.participants, dp.participants                   # honorific -> participant
    assert any(s["kind"] == "lost" for s in shifts), shifts                 # TIME LOST -> clock shift, not an event
    print("events:", sorted(titles), "| shifts:", len(shifts))
    print("OK")


if __name__ == "__main__":
    main()
