"""Layer-1 quality control. Every check is deterministic and produces QCIssues plus, where useful,
flags/links written back onto units (e.g. `duplicate_of`, `copy_of`) so the extraction layer can skip
redundant text while keeping every copy as an alternative citation."""
from __future__ import annotations
import re
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from .models import QCIssue
from .normalize import find_dates, find_money, find_sensitive, is_relay


def _sim(a: str, b: str) -> float:
    a, b = re.sub(r"\W+", " ", a.lower())[:2000], re.sub(r"\W+", " ", b.lower())[:2000]
    return SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0.0


def _name_key(name: str | None) -> str | None:
    if not name:
        return None
    toks = sorted(t for t in re.findall(r"[a-z]+", name.lower()) if t not in ("mr", "ms", "mrs", "dr", "prof"))
    return " ".join(toks) or None


FIELD_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z /()#-]{1,40}?)\s*:\s+(\S.{0,150})$", re.M)
GREETING = re.compile(r"\bgood (morning|afternoon|evening|night)\b", re.I)
GREETING_HOURS = {"morning": (4, 12), "afternoon": (12, 17), "evening": (16, 23), "night": (20, 4)}


def _fields(text: str) -> dict:
    """'Room:  Park Hall' style lines; repeated whitespace-separated 'k: v   k2: v2' pairs are split."""
    out = {}
    for k, v in FIELD_LINE.findall(text):
        for kk, vv in [(k, v)] + re.findall(r"\s{3,}([A-Za-z][A-Za-z ]{1,30}?)\s*:\s+(\S[^:]*?)(?=\s{3,}|$)", v):
            out[kk.strip().lower()] = re.split(r"\s{3,}", vv.strip())[0].strip()
    return out


def _same_value(a: str, b: str) -> bool:
    if a == b:
        return True
    try:
        from dateutil import parser as p
        return p.parse(a, fuzzy=True).date() == p.parse(b, fuzzy=True).date() and re.search(r"\d{4}", a) and re.search(r"\d{4}", b)
    except Exception:
        return False


def _field_changes(old: dict, new: dict) -> list:
    return [(k, old[k], new[k]) for k in old if k in new and not _same_value(old[k], new[k])]


def _file_key(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"^\d{6,10}-", "", name)          # WhatsApp prefixes saved attachments with a counter
    return re.sub(r"[\s_]+", "_", name)


def run_qc(sources: dict, units: list) -> list[QCIssue]:
    issues: list[QCIssue] = []
    by_id = {u.unit_id: u for u in units}
    emails = [u for u in units if u.unit_type == "email"]
    embedded = [u for u in units if u.unit_type in ("quoted_email", "forwarded_email")]

    # 0. files nobody could read: say so instead of silently ignoring them
    skipped = [x for x in sources.values() if x.meta.get("skipped")]
    if skipped:
        issues.append(QCIssue("unsupported_files", "warning",
            f"{len(skipped)} file(s) could not be read: " + ", ".join(f"{x.filename} ({x.meta['skipped']})" for x in skipped[:8]),
            [x.source_id for x in skipped], details={"files": [[x.filename, x.detected_type] for x in skipped]},
            suggested_action="Convert to PDF, text or a supported export (WhatsApp .txt/.zip, .eml/.mbox, SMS Backup XML)."))

    # 1. identical files (same bytes under different names / as an email attachment)
    for s in sources.values():
        if s.aliases:
            issues.append(QCIssue("exact_duplicate_file", "info",
                f"'{s.filename}' appears {len(s.aliases) + 1} times with identical bytes; stored once.",
                [s.source_id], details={"locations": [s.path] + s.aliases},
                suggested_action="None needed; all locations are kept as alternate citations."))

    # 2. the same email present twice (single .eml download + Takeout mbox)
    by_mid = defaultdict(list)
    for u in emails:
        if u.locator.message_id:
            by_mid[u.locator.message_id].append(u)
    for mid, group in by_mid.items():
        if len(group) > 1:
            keep = sorted(group, key=lambda u: (u.locator.message_index is None, u.unit_id))[0]
            for dup in group:
                if dup is not keep:
                    dup.flags.append("duplicate"); dup.meta["duplicate_of"] = keep.unit_id
                    keep.meta.setdefault("alternate_locators", []).append(dup.locator.__dict__)
            dup_ids = {d.unit_id for d in group if d is not keep}
            for e in embedded:
                if e.meta.get("embedded_in") in dup_ids:
                    e.flags.append("duplicate")
            issues.append(QCIssue("duplicate_message", "info",
                f"Message '{keep.meta['subject'][:70]}' exists {len(group)} times (Message-ID match).",
                list({g.locator.source_id for g in group}), [g.unit_id for g in group],
                suggested_action="Extraction runs on one copy; citations can point to any."))

    # 3. embedded copies (quotes / forwards): link to an existing email, or cluster the "lost" ones
    lost = []
    for e in embedded:
        if "duplicate" in e.flags:
            continue
        best, score = None, 0.0
        for u in emails:
            if u.unit_id == e.meta.get("embedded_in") or "duplicate" in u.flags:
                continue
            s = _sim(e.text, u.text)
            if s > score:
                best, score = u, s
        if best and score >= 0.8:
            e.flags.append("copy_of_existing"); e.meta["copy_of"] = best.unit_id; e.meta["copy_similarity"] = round(score, 2)
        elif len(e.text) > 40:
            lost.append(e)
    clusters: list[list] = []                      # the same lost message is often quoted in every later reply
    for e in sorted(lost, key=lambda e: -len(e.text)):
        for c in clusters:
            if _sim(e.text, c[0].text) >= 0.8:
                c.append(e); break
        else:
            clusters.append([e])
    for c in clusters:
        rep = c[0]                                 # longest copy = least trimmed
        rep.flags.append("only_surviving_copy")
        for other in c[1:]:
            other.flags.append("copy_of_existing"); other.meta["copy_of"] = rep.unit_id
        who = (rep.author or {}).get("address") or (rep.author or {}).get("display")
        issues.append(QCIssue("recovered_from_quote", "warning",
            f"A {'forwarded' if rep.unit_type == 'forwarded_email' else 'quoted'} message from {who or 'unknown sender'} "
            f"({rep.timestamp_raw or 'no date'}) is missing as a standalone email; recovered from {len(c)} quote(s).",
            sorted({x.locator.source_id for x in c}), [x.unit_id for x in c],
            details={"subject_key": rep.meta.get("subject_key"), "preview": rep.text[:160],
                     "relayed_by": (rep.meta.get("relayed_by") or {}).get("address")},
            suggested_action="Use as evidence with 'reconstructed' confidence; try to obtain the original."))

    # 4. thread integrity: replies whose parent is missing
    known = set(by_mid)
    for u in emails:
        parent = u.meta.get("in_reply_to")
        if parent and parent not in known and "duplicate" not in u.flags:
            recovered = [e for e in embedded if e.meta.get("embedded_in") == u.unit_id and e.meta.get("depth") == 1]
            issues.append(QCIssue("missing_thread_parent", "warning" if recovered else "error",
                f"'{u.meta['subject'][:70]}' replies to a message that is not in the dump"
                + (" (its text was recovered from the quote)." if recovered else "."),
                [u.locator.source_id], [u.unit_id] + [r.unit_id for r in recovered],
                details={"missing_message_id": parent},
                suggested_action="Search other exports/mailboxes for this Message-ID."))
    for u in emails:
        missing_refs = [r for r in u.meta.get("references", []) if r not in known and r != u.meta.get("in_reply_to")]
        if missing_refs and "duplicate" not in u.flags:
            u.meta["missing_ancestors"] = missing_refs

    # 5. attachment mentioned but not present
    for u in emails:
        if "duplicate" in u.flags or u.meta.get("boundary_kind") == "forward":
            continue                               # a forward carries its content inline
        if re.search(r"\b(attached|attachment|please find enclosed|PFA)\b", u.text, re.I) and not u.meta.get("attachments"):
            issues.append(QCIssue("attachment_mentioned_not_present", "warning",
                f"'{u.meta['subject'][:70]}' mentions an attachment but none is in the file.",
                [u.locator.source_id], [u.unit_id]))

    # 6. WhatsApp: dates, timezone, missing media, deletions, silences
    for s in sources.values():
        wa = s.meta.get("whatsapp")
        if not wa:
            continue
        if wa.get("kind") == "sms":
            pass
        elif wa["day_first"] is None:
            issues.append(QCIssue("chat_date_order_ambiguous", "error",
                f"Cannot tell DD/MM from MM/DD in '{s.filename}'.", [s.source_id],
                suggested_action="Ask for the phone's locale or set it in config."))
        if wa.get("kind") != "sms":
          issues.append(QCIssue("chat_timezone_assumed", "info",
            f"'{s.filename}' has no timezone; assumed UTC{wa['assumed_tz_hours']:+g}.", [s.source_id],
            details={"participants": wa["participants"]}))
        chat = sorted([u for u in units if u.locator.source_id == s.source_id and u.timestamp_utc],
                      key=lambda u: u.timestamp_utc)
        media = [u for u in chat if "media_missing_from_export" in u.flags]
        if media:
            issues.append(QCIssue("chat_media_missing", "warning",
                f"{len(media)} media items in '{s.filename}' were exported as placeholders only.",
                [s.source_id], [u.unit_id for u in media],
                details={"items": [u.meta["media"] for u in media]},
                suggested_action="Re-export 'with media' or upload the referenced files; the knowledge graph will mark these as known-missing evidence."))
        deleted = [u for u in chat if "deleted_message" in u.flags]
        if deleted:
            issues.append(QCIssue("chat_deleted_messages", "info", f"{len(deleted)} deleted message(s).",
                                  [s.source_id], [u.unit_id for u in deleted]))
        for a, b in zip(chat, chat[1:]):
            gap = (datetime.fromisoformat(b.timestamp_utc) - datetime.fromisoformat(a.timestamp_utc)).days
            if gap >= 14:
                issues.append(QCIssue("chat_silence", "info", f"{gap}-day gap in '{s.filename}' "
                    f"({a.timestamp_raw} → {b.timestamp_raw}); possibly disappearing messages or another channel.",
                    [s.source_id], [a.unit_id, b.unit_id]))

    # 7. documents: parser health, cross-check, mixed date conventions, multi-document files
    for s in sources.values():
        doc = s.meta.get("document")
        if not doc:
            continue
        pages = [u for u in units if u.locator.source_id == s.source_id and u.unit_type == "doc_page"]
        if doc.get("needs_parser"):
            issues.append(QCIssue("no_parser_output", "error", f"No text could be obtained from '{s.filename}'.",
                                  [s.source_id], suggested_action="Set LLAMA_CLOUD_API_KEY (OCR needed)."))
        for p in pages:
            cc = p.meta.get("cross_check")
            if cc and cc["recall"] is not None and cc["recall"] < 0.95:
                issues.append(QCIssue("parser_dropped_values", "warning",
                    f"Page {p.locator.page} of '{s.filename}': {int((1 - cc['recall']) * 100)}% of numbers/codes in the "
                    f"PDF text layer are missing from the parsed output.", [s.source_id], [p.unit_id],
                    details=cc, suggested_action="Re-parse this page with a higher tier or use the native text for these values."))
            conf = p.meta.get("llamaparse_confidence")
            if conf is not None and conf < 0.8:
                issues.append(QCIssue("low_parse_confidence", "warning",
                    f"LlamaParse confidence {conf:.2f} on page {p.locator.page} of '{s.filename}'.", [s.source_id], [p.unit_id]))
            if "near_empty_page" in p.flags:
                issues.append(QCIssue("near_empty_page", "warning", f"Page {p.locator.page} of '{s.filename}' has almost no text.",
                                      [s.source_id], [p.unit_id]))
            if "image_only_page_needs_ocr" in p.flags:
                issues.append(QCIssue("image_only_page_needs_ocr", "warning",
                    f"Page {p.locator.page} of '{s.filename}' is essentially an image (text layer: "
                    f"{len(p.text)} chars). Its content is unreadable until OCR runs.", [s.source_id], [p.unit_id],
                    suggested_action="Run with LLAMA_CLOUD_API_KEY; sparse pages are routed to the agentic (OCR) tier."))
        orders = defaultdict(list)
        for p in pages:
            for d in find_dates(p.raw_text or p.text):
                orders[d["order"]].append(f"p{p.locator.page}: {d['raw']}")
        if orders.get("day_first") and orders.get("month_first"):
            issues.append(QCIssue("mixed_date_conventions", "warning",
                f"'{s.filename}' uses both DD/MM and MM/DD dates.", [s.source_id],
                details={"day_first": orders["day_first"][:5], "month_first": orders["month_first"][:5]},
                suggested_action="Resolve each date by its own unambiguous siblings, never by a file-level default."))
        if orders.get("ambiguous"):
            issues.append(QCIssue("ambiguous_dates", "info",
                f"'{s.filename}' contains dates where day and month are both ≤ 12.", [s.source_id],
                details={"examples": orders["ambiguous"][:5]}))
        markers = sorted({m for p in pages for m in p.meta.get("plan_markers", [])})
        if len(markers) >= 2:
            for p in pages:
                p.flags.append("forward_looking")
            issues.append(QCIssue("forward_looking_document", "warning",
                f"'{s.filename}' reads as a plan, not a record (markers: {', '.join(markers)}).", [s.source_id],
                [p.unit_id for p in pages],
                suggested_action="Extract its events as 'planned'/'assumed' and confirm against tickets, chats or receipts."))
        types = sorted({(p.meta["segment"], p.meta["doc_type_hint"] or "unknown") for p in pages})
        if len(types) > 1:
            issues.append(QCIssue("multi_document_file", "info",
                f"'{s.filename}' contains {len(types)} logical documents: " + ", ".join(t for _, t in types) + ".",
                [s.source_id], details={"segments": types},
                suggested_action="Downstream, treat each segment as its own document for citations."))

    # 8. money and currencies across the corpus
    seen = defaultdict(list)
    for u in units:
        if "duplicate" in u.flags or "copy_of_existing" in u.flags:
            continue
        for m in find_money(u.text):
            seen[m["currency"]].append({"unit": u.unit_id, "raw": m["raw"]})
    if seen:
        issues.append(QCIssue("currency_inventory", "info",
            "Currencies found: " + ", ".join(f"{k} ({len(v)})" for k, v in seen.items()) + ".",
            details={k: v[:8] for k, v in seen.items()},
            suggested_action="Normalise in layer 2 using the rate on each transaction date; keep originals."))
    if "USD?" in seen:
        issues.append(QCIssue("ambiguous_currency_symbol", "warning",
            f"{len(seen['USD?'])} amounts use a bare '$' (could be USD, CAD, AUD, SGD...).",
            unit_ids=[x["unit"] for x in seen["USD?"]][:20],
            suggested_action="Confirm from context (sender country, invoice footer) before conversion."))

    # 9. sensitive data before anything leaves the machine
    for u in units:
        hits = find_sensitive(u.text)
        if hits:
            u.flags.append("contains_sensitive")
            u.meta["sensitive_spans"] = [(h["kind"], h["start"], h["end"]) for h in hits]
            issues.append(QCIssue("sensitive_data", "warning",
                f"Possible {', '.join(sorted({h['kind'] for h in hits}))} in {u.unit_type} ({u.timestamp_raw or 'p' + str(u.locator.page)}).",
                [u.locator.source_id], [u.unit_id],
                suggested_action="Mask before sending to external LLM APIs; keep raw only in the local store."))


    # 11. same sender + same subject, different Message-ID: a resend, or a revised notice whose facts changed
    groups = defaultdict(list)
    for u in emails:
        if "duplicate" not in u.flags and u.author:
            groups[(u.author.get("canonical"), u.meta["subject_key"])].append(u)
    for (sender, subj), g in groups.items():
        g.sort(key=lambda u: u.timestamp_utc or "")
        for old, new in zip(g, g[1:]):
            sim = _sim(old.text, new.text)
            if sim >= 0.97:
                new.flags.append("resent"); new.meta["resend_of"] = old.unit_id
                issues.append(QCIssue("resent_message", "info",
                    f"'{new.meta['subject'][:70]}' was sent again ({old.timestamp_utc} and {new.timestamp_utc}) with the same text.",
                    sorted({old.locator.source_id, new.locator.source_id}), [old.unit_id, new.unit_id]))
                continue
            changes = _field_changes(_fields(old.text), _fields(new.text))
            if changes:
                old.meta["superseded_by"] = new.unit_id; old.flags.append("superseded")
                new.meta["supersedes"] = old.unit_id
                issues.append(QCIssue("revised_notice", "warning",
                    f"'{new.meta['subject'][:70]}' was updated between {old.timestamp_utc[:10]} and {new.timestamp_utc[:10]}: "
                    + "; ".join(f"{k}: '{a}' → '{b}'" for k, a, b in changes[:4]),
                    sorted({old.locator.source_id, new.locator.source_id}), [old.unit_id, new.unit_id],
                    details={"changes": changes, "similarity": round(sim, 2)},
                    suggested_action="Use the newest values as current; keep the old ones as history (valid_until)."))

    # 12. WhatsApp attachments that match files in the dump -> provenance (who shared it, when)
    index = {}
    for src in sources.values():
        for name in [src.filename] + [a.rsplit("/", 1)[-1] for a in src.aliases]:
            index[_file_key(name)] = src
    linked, unresolved = [], []
    for u in units:
        media = u.meta.get("media")
        if not media or not media.get("filename"):
            continue
        hit = index.get(_file_key(media["filename"]))
        if hit:
            media["source_id"] = hit.source_id; u.flags.append("media_linked")
            hit.meta.setdefault("shared_in_chat", []).append({"unit_id": u.unit_id, "by": (u.author or {}).get("display"),
                                                              "at": u.timestamp_utc})
            linked.append((u, hit))
        elif media.get("kind") in ("document", "attached"):
            unresolved.append(u)
    for u, hit in linked:
        issues.append(QCIssue("chat_attachment_linked", "info",
            f"'{hit.filename}' is the file {(u.author or {}).get('display')} shared in chat on {u.timestamp_raw}.",
            [u.locator.source_id, hit.source_id], [u.unit_id]))
    if unresolved:
        issues.append(QCIssue("chat_documents_not_in_dump", "warning",
            f"{len({u.meta['media']['filename'] for u in unresolved})} distinct documents shared in chats ({len(unresolved)} shares) are not in the dump.",
            unit_ids=[u.unit_id for u in unresolved],
            details={"files": sorted({u.meta['media']['filename'] for u in unresolved})},
            suggested_action="Upload these files; they are cited by the chats but their contents are unknown."))

    # 13. chat timestamp sanity: out-of-order lines, and greetings that don't fit the assumed clock
    for src in sources.values():
        if not src.meta.get("whatsapp"):
            continue
        chat = [u for u in units if u.locator.source_id == src.source_id and u.timestamp_utc]
        chat.sort(key=lambda u: u.locator.line_start)
        for a, b in zip(chat, chat[1:]):
            if b.timestamp_utc < a.timestamp_utc and (datetime.fromisoformat(a.timestamp_utc) - datetime.fromisoformat(b.timestamp_utc)).seconds > 60:
                issues.append(QCIssue("chat_out_of_order", "info",
                    f"'{src.filename}' line {b.locator.line_start} ({b.timestamp_raw}) is earlier than the line before it "
                    f"({a.timestamp_raw}); usually a delayed delivery or a system note.", [src.source_id], [a.unit_id, b.unit_id]))
        odd = defaultdict(list)
        for u in chat:
            g = GREETING.search(u.text or "")
            if g and u.meta.get("local_time"):
                hour = datetime.fromisoformat(u.meta["local_time"]).hour
                lo, hi = GREETING_HOURS[g.group(1).lower()]
                if not (lo <= hour < hi if lo < hi else hour >= lo or hour < hi):
                    odd[(u.author or {}).get("display")].append(u)
        for who, us in odd.items():
            issues.append(QCIssue("sender_timezone_hint", "info",
                f"In '{src.filename}', {who} says '{GREETING.search(us[0].text).group(0)}' at "
                + ", ".join(f"{datetime.fromisoformat(u.meta['local_time']):%d %b %H:%M}" for u in us[:4])
                + f" (export clock UTC{src.meta['whatsapp']['assumed_tz_hours']:+g}); they were probably in another timezone then.",
                [src.source_id], [u.unit_id for u in us],
                suggested_action="Layer 2: derive the sender's location timezone from travel evidence and store event-local times."))

    # 10. identity inventory: one name, many addresses -> merge candidates for layer 2
    by_name = defaultdict(set)
    for u in units:
        for p in ([u.author] if u.author else []) + u.recipients:
            k = _name_key(p.get("display"))
            if k and p.get("canonical") and not is_relay(p.get("address")):
                by_name[k].add(p["canonical"])
    canon_by_local = defaultdict(set)
    for u in units:
        for p in ([u.author] if u.author else []) + u.recipients:
            if p.get("address"):
                canon_by_local[p["canonical"]].add(p["address"])
    for name, addrs in by_name.items():
        if len(addrs) > 1:
            issues.append(QCIssue("identity_merge_candidate", "info",
                f"'{name}' appears with {len(addrs)} different addresses.", details={"addresses": sorted(addrs)},
                suggested_action="Confirm in the entity-resolution step; chat aliases (e.g. nicknames) need a manual mapping."))
    for canon, variants in canon_by_local.items():
        if len(variants) > 1:
            issues.append(QCIssue("address_variants_merged", "info",
                f"{sorted(variants)} were normalised to {canon}.", details={"variants": sorted(variants)}))

    for u in emails:
        if "html_only_body" in u.flags and "duplicate" not in u.flags:
            issues.append(QCIssue("html_only_email", "info",
                f"'{u.meta['subject'][:70]}' had no text/plain part; converted from HTML.", [u.locator.source_id], [u.unit_id]))
    return issues
