"""The knowledge graph, stored in SQLite so the demo needs no server.

Nodes   entities (people, orgs, places), events (with a status: planned / happened / cancelled / reported_state)
Edges   event_participants, evidence (event -> unit + exact quote + char span), insights (plan vs evidence verdicts)
Every row that states something about the world has at least one evidence row pointing to a layer-1 unit.
"""
from __future__ import annotations
import json, sqlite3
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

EVENT_STATUSES = ("planned", "scheduled", "happened", "cancelled", "reported_state", "unknown")
# planned        a personal intention or draft plan ("I will go", an itinerary spreadsheet)
# scheduled      booked or officially fixed by someone else (ticket, appointment letter, programme email)
# happened       reported as done ("I reached", receipt paid, "presented today")
# cancelled      explicitly called off or changed
# reported_state a condition someone reports (ill, at the hotel, busy)


@dataclass
class Evidence:
    unit_id: str
    quote: str
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    match: str = "unverified"          # exact | normalized | fuzzy | unverified
    note: Optional[str] = None         # e.g. grid cell range "D12:D19"


@dataclass
class Event:
    event_id: str
    kind: str                          # travel | presentation | meeting | accommodation | health | payment | deadline | communication | other
    title: str
    status: str
    start_local: Optional[str] = None  # ISO, local wall-clock of where it happened (naive)
    end_local: Optional[str] = None
    tz_offset_hours: Optional[float] = None
    time_confidence: str = "none"      # exact | resolved_relative | date_only | assumed_tz | unknown | none
    time_expression: Optional[str] = None
    location: Optional[str] = None
    participants: list[str] = field(default_factory=list)   # raw names; resolved via event_participants
    amount: Optional[float] = None
    currency: Optional[str] = None
    tentative: bool = False
    extracted_by: str = "llm"          # llm | grid | rule
    evidence: list[Evidence] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (entity_id TEXT PRIMARY KEY, type TEXT, name TEXT, is_self INTEGER DEFAULT 0,
    aliases TEXT, identifiers TEXT, meta TEXT);
CREATE TABLE IF NOT EXISTS mentions (entity_id TEXT, surface TEXT, unit_id TEXT);
CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, kind TEXT, title TEXT, status TEXT, start_local TEXT,
    end_local TEXT, tz_offset_hours REAL, time_confidence TEXT, time_expression TEXT, location TEXT, amount REAL,
    currency TEXT, tentative INTEGER, extracted_by TEXT, participants_raw TEXT, meta TEXT);
CREATE TABLE IF NOT EXISTS event_participants (event_id TEXT, entity_id TEXT);
CREATE TABLE IF NOT EXISTS evidence (event_id TEXT, unit_id TEXT, quote TEXT, char_start INTEGER, char_end INTEGER,
    match TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS merge_proposals (a TEXT, b TEXT, score REAL, reason TEXT, status TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS insights (insight_id TEXT PRIMARY KEY, kind TEXT, verdict TEXT, confidence TEXT, summary TEXT,
    plan_event_id TEXT, evidence_event_ids TEXT, citations TEXT, review_status TEXT DEFAULT 'pending', reviewer_note TEXT);
CREATE INDEX IF NOT EXISTS ev_time ON events(start_local);
CREATE INDEX IF NOT EXISTS evid_event ON evidence(event_id);
"""


class GraphStore:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # -------------------------------------------------------------- writes
    def add_event(self, e: Event):
        self.db.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            e.event_id, e.kind, e.title, e.status, e.start_local, e.end_local, e.tz_offset_hours, e.time_confidence,
            e.time_expression, e.location, e.amount, e.currency, int(e.tentative), e.extracted_by,
            json.dumps(e.participants, ensure_ascii=False), json.dumps(e.meta, ensure_ascii=False, default=str)))
        self.db.execute("DELETE FROM evidence WHERE event_id=?", (e.event_id,))
        for ev in e.evidence:
            self.db.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?)",
                            (e.event_id, ev.unit_id, ev.quote, ev.char_start, ev.char_end, ev.match, ev.note))

    def add_entity(self, entity_id, etype, name, is_self=False, aliases=(), identifiers=(), meta=None):
        self.db.execute("INSERT OR REPLACE INTO entities VALUES (?,?,?,?,?,?,?)", (
            entity_id, etype, name, int(is_self), json.dumps(sorted(set(aliases)), ensure_ascii=False),
            json.dumps(sorted(set(identifiers))), json.dumps(meta or {}, ensure_ascii=False)))

    def link(self, event_id, entity_id):
        self.db.execute("INSERT INTO event_participants VALUES (?,?)", (event_id, entity_id))

    def add_insight(self, row: dict):
        self.db.execute("INSERT OR REPLACE INTO insights VALUES (?,?,?,?,?,?,?,?,?,?)", (
            row["insight_id"], row["kind"], row["verdict"], row["confidence"], row["summary"], row["plan_event_id"],
            json.dumps(row["evidence_event_ids"]), json.dumps(row["citations"], ensure_ascii=False),
            row.get("review_status", "pending"), row.get("reviewer_note")))

    def review(self, table: str, key_col: str, key: str, status: str, note: str | None = None):
        if table == "insights":
            self.db.execute("UPDATE insights SET review_status=?, reviewer_note=? WHERE insight_id=?", (status, note, key))
        elif table == "merge_proposals":
            a, b = key.split("|", 1)
            self.db.execute("UPDATE merge_proposals SET status=? WHERE a=? AND b=?", (status, a, b))
        self.db.commit()

    def commit(self):
        self.db.commit()

    # -------------------------------------------------------------- reads
    def q(self, sql: str, args=()) -> list[dict]:
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def events(self, where: str = "1=1", args=()) -> list[dict]:
        rows = self.q(f"SELECT * FROM events WHERE {where} ORDER BY start_local", args)
        for r in rows:
            r["participants_raw"] = json.loads(r["participants_raw"] or "[]")
            r["meta"] = json.loads(r["meta"] or "{}")
            r["evidence"] = self.q("SELECT * FROM evidence WHERE event_id=?", (r["event_id"],))
            r["entity_ids"] = [x["entity_id"] for x in self.q("SELECT entity_id FROM event_participants WHERE event_id=?", (r["event_id"],))]
        return rows
