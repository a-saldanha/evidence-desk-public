"""Orchestrates layer 1:  register -> route -> parse -> QC -> write outputs.

Outputs (in --out):
  sources.json      every physical file, its hash, type, aliases, parent email
  units.jsonl       one citable unit per line (what layer 2 extracts from)
  qc_issues.jsonl   machine-readable issues
  qc_report.md      human-readable summary
  raw/              content-addressed copies of every original (never modified)
"""
from __future__ import annotations
import argparse, json
from collections import Counter
from pathlib import Path
from .store import SourceStore
from .parsers.email_parser import EmailParser
from .parsers.whatsapp_parser import WhatsAppParser
from .parsers.document_parser import DocumentParser
from .parsers import misc_parsers as misc
from .qc import run_qc
from .models import Unit, Locator, stable_id


def calendar_unit(store, s) -> Unit:
    ics = store.raw_bytes(s.source_id).decode("utf-8", "replace")
    cal = EmailParser._calendar(ics)
    return Unit(unit_id=stable_id(s.source_id, "ics"), unit_type="calendar_event",
                locator=Locator(source_id=s.source_id, kind="attachment"),
                text=f"{cal['summary']} | start {cal['dtstart']} | end {cal['dtend']} | organizer {cal['organizer']}",
                raw_text=ics, timestamp_raw=cal["dtstart"], meta={"calendar": cal})


def run(inputs: list[Path], out: Path, offline: bool = False, tz_hours: float = 5.5,
        chat_aliases: dict | None = None, version: str = "latest"):
    out.mkdir(parents=True, exist_ok=True)
    store = SourceStore(out)
    ep = EmailParser(store)
    wp = WhatsAppParser(store, default_tz_hours=tz_hours, self_aliases=chat_aliases)
    dp = DocumentParser(store, out / "parse_cache", offline=offline, version=version)

    files = []
    for p in inputs:
        files += sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else [p]
    for f in files:
        store.add_path(f)

    units, done = [], set()
    # loop until no new sources appear (email attachments are registered while parsing)
    while True:
        pending = [s for s in store.sources.values() if s.source_id not in done]
        if not pending:
            break
        for s in sorted(pending, key=lambda s: s.detected_type not in ("mbox", "email")):
            done.add(s.source_id)
            t = s.detected_type
            try:
                if t == "mbox":
                    units += ep.parse_mbox(s)
                elif t == "email":
                    units += ep.parse_eml(s)
                elif t == "whatsapp":
                    units += wp.parse(s)
                elif t in ("pdf", "image"):
                    units += dp.parse(s)
                elif t == "calendar":
                    units.append(calendar_unit(store, s))
                elif t == "text":
                    units += misc.parse_text(store, s)
                elif t == "docx":
                    units += misc.parse_docx(store, s)
                elif t == "xlsx":
                    units += misc.parse_xlsx_text(store, s)     # time grids are also picked up in layer 2
                elif t == "sms_backup":
                    units += misc.parse_sms_backup(store, s, chat_aliases or {}, tz_hours)
                elif t == "empty":
                    s.meta["skipped"] = "empty file"
                else:
                    s.meta["skipped"] = f"no parser for type '{t}'"
            except Exception as e:                              # one malformed file must not kill the whole ingest
                s.meta["skipped"] = f"parse error ({t}): {type(e).__name__}: {str(e)[:120]}"

    issues = run_qc(store.sources, units)
    store.dump(out / "sources.json")
    (out / "units.jsonl").write_text("\n".join(u.to_json() for u in units) + "\n", encoding="utf-8")
    (out / "qc_issues.jsonl").write_text("\n".join(i.to_json() for i in issues) + "\n", encoding="utf-8")
    (out / "qc_report.md").write_text(render_report(store, units, issues, dp), encoding="utf-8")
    return store, units, issues


def render_report(store, units, issues, dp) -> str:
    sev = Counter(i.severity for i in issues)
    utypes = Counter(u.unit_type for u in units)
    L = ["# Ingestion & QC report", "",
         f"Sources: **{len(store.sources)}** unique files · Units: **{len(units)}** · "
         f"Issues: {sev.get('error', 0)} errors, {sev.get('warning', 0)} warnings, {sev.get('info', 0)} info",
         f"Document backend: {'LlamaParse' if not dp.offline else 'native PDF text layer (offline mode; set LLAMA_CLOUD_API_KEY for LlamaParse)'}",
         "", "## Sources", "", "| File | Detected type | Units | Notes |", "|---|---|---|---|"]
    per_src = Counter(u.locator.source_id for u in units)
    for s in store.sources.values():
        notes = []
        if s.aliases: notes.append(f"identical copy at {len(s.aliases)} other location(s)")
        if s.parent_unit_id: notes.append("extracted from an email attachment")
        if s.meta.get("skipped"): notes.append(s.meta["skipped"])
        if (d := s.meta.get("document")): notes.append(f"backend: {d['backend']}")
        if (w := s.meta.get("whatsapp")): notes.append(f"{w['messages']} msgs, DD/MM={w['day_first']}")
        L.append(f"| {s.filename} | {s.detected_type} | {per_src.get(s.source_id, 0)} | {'; '.join(notes)} |")
    L += ["", "## Unit types", "", ", ".join(f"{k}: {v}" for k, v in utypes.most_common()), ""]
    order = {"error": 0, "warning": 1, "info": 2}
    L += ["## Issues", ""]
    for check in sorted({i.check for i in issues}, key=lambda c: min(order[i.severity] for i in issues if i.check == c)):
        group = [i for i in issues if i.check == check]
        L.append(f"### {check} ({group[0].severity}, {len(group)})")
        L.append("")
        for i in group[:12]:
            L.append(f"- {i.message}")
            if i.suggested_action and i is group[0]:
                L.append(f"  - Action: {i.suggested_action}")
        if len(group) > 12:
            L.append(f"- … {len(group) - 12} more in qc_issues.jsonl")
        L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Layer-1 ingestion and QC")
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("ingest_out"))
    ap.add_argument("--offline", action="store_true", help="skip LlamaParse, use PDF text layers only")
    ap.add_argument("--chat-tz", type=float, default=5.5, help="UTC offset assumed for chat exports")
    ap.add_argument("--chat-alias", action="append", default=[], help="'Jane=jane.smith@example.com'")
    ap.add_argument("--llamaparse-version", default="latest", help="pin a dated tier version in production")
    a = ap.parse_args()
    aliases = dict(x.split("=", 1) for x in a.chat_alias)
    store, units, issues = run(a.inputs, a.out, a.offline, a.chat_tz, aliases, a.llamaparse_version)
    print(f"{len(store.sources)} sources, {len(units)} units, {len(issues)} issues -> {a.out}/qc_report.md")


if __name__ == "__main__":
    main()
