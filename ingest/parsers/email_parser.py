"""Parses .eml and .mbox (Google Takeout) into Units.

Per message we produce:
  * one `email` unit holding only the NEW text the sender wrote (quotes and signature removed),
  * `quoted_email` / `forwarded_email` units for earlier messages embedded in the body. These are kept,
    not thrown away, because the quote is sometimes the only surviving copy of a missing message,
  * attachments handed back to the store (deduplicated by hash, linked to the parent unit).
Headers are parsed with the stdlib, never by an LLM.
"""
from __future__ import annotations
import email, mailbox, re
from email import policy
from email.message import EmailMessage
from typing import Iterator, Optional
from bs4 import BeautifulSoup
from dateutil import parser as dtparser

from ..models import Unit, Locator, stable_id
from ..normalize import (clean_text, strip_link_artifacts, parse_addresses, normalize_message_id, email_date_to_utc)

SUBJECT_PREFIX = re.compile(r"^\s*((re|fw|fwd|aw|sv|tr|\[resend\]|\[external\])\s*:?\s*)+", re.I)

# Boundaries where older content starts. Order matters: earliest match in the body wins.
REPLY_HEADER = re.compile(r"^(?:>\s*)?On [^\n]{3,200}?(?:\n(?:>\s*)?[^\n]{0,200}?){0,3}\bwrote:\s*$", re.M)
FORWARD_HEADER = re.compile(r"^-{5,}\s*Forwarded message\s*-{5,}\s*$|^Begin forwarded message:\s*$", re.M | re.I)
OUTLOOK_HEADER = re.compile(r"^(?:-{3,}\s*Original Message\s*-{3,}|_{10,})?\s*\n?^From:\s.+\n(?:Sent|Date):\s.+\n(?:To:\s.+\n)?", re.M)
# Banners injected by corporate gateways / Outlook; pure noise for extraction (kept in raw_text).
BOILERPLATE = [
    re.compile(r"^.*You don't often get email from .*?Learn why this is important<[^>]*>\s*", re.M),
    re.compile(r"^\*{3,}\s*THE E-MAIL BELOW IS FROM AN EXTERNAL SOURCE.*?\*{3,}\s*$", re.M | re.I),
    re.compile(r"^\[?\s*(?:CAUTION|EXTERNAL EMAIL)\b[^\n]{0,200}$", re.M | re.I),
]
TIME_TOKEN = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?\s?(?:[AaPp]\.?[Mm]\.?)?(?:\s?(?:[+-]\d{4}|UTC|GMT|IST|CET|CEST|EET|EEST))?")
SIGNATURE = re.compile(r"^-- ?$|^Sent from my (?:iPhone|iPad|Android|Galaxy|mobile)|^Get Outlook for", re.M)
CONTACT_FORM = re.compile(r"submitted via contact form", re.I)


def subject_key(subj: str) -> str:
    s = re.sub(r"\s+", " ", subj or "")
    return SUBJECT_PREFIX.sub("", s).strip().lower()


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["style", "script", "head"]):
        t.decompose()
    for bq in soup.find_all("blockquote"):          # keep quotes recognisable after flattening
        bq.insert_before("\n>>QUOTE_START\n")
        bq.insert_after("\n>>QUOTE_END\n")
    for br in soup.find_all(["br", "p", "div", "tr", "li"]):
        br.insert_after("\n")
    return soup.get_text()


def best_body(msg: EmailMessage) -> tuple[str, str]:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return "", "none"
    try:
        content = part.get_content()
    except Exception:
        content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    if part.get_content_type() == "text/html":
        return html_to_text(content), "html"
    return content, "plain"


def split_body(body: str) -> dict:
    """Return {'new': (start,end), 'boundary_kind': ..., 'rest': (start,end), 'signature': (start,end)|None}."""
    candidates = []
    for kind, rx in (("reply", REPLY_HEADER), ("forward", FORWARD_HEADER), ("outlook", OUTLOOK_HEADER)):
        m = rx.search(body)
        if m:
            candidates.append((m.start(), kind, m))
    q = re.search(r"^>.*\n>.*$", body, re.M)               # bare '>' quoting (2+ lines) without a header
    if q:
        candidates.append((q.start(), "bare_quote", q))
    if not candidates:
        new_end, kind, rest = len(body), None, None
    else:
        start, kind, _ = min(candidates, key=lambda c: c[0])
        new_end, rest = start, (start, len(body))
    sig = SIGNATURE.search(body, 0, new_end)
    return {"new": (0, sig.start() if sig else new_end), "boundary_kind": kind, "rest": rest,
            "signature": (sig.start(), new_end) if sig else None}


def strip_boilerplate(text: str) -> tuple[str, int]:
    n = 0
    for rx in BOILERPLATE:
        text, k = rx.subn("", text)
        n += k
    return text, n


def unquote(text: str) -> str:
    return "\n".join(re.sub(r"^>\s?", "", l) for l in text.split("\n"))


def parse_attribution(header_text: str) -> dict:
    """'On Mon, Jun 15, 2026 at 6:27 AM Jane Smith <jane.smith@example.com> wrote:'"""
    h = " ".join(unquote(header_text).split())
    out = {"raw": h, "author": None, "date_raw": None, "date_parsed_local": None}
    addr = re.search(r"([^<>,]*?)\s*<\s*([^<>\s]+@[^<>\s]+)\s*>", h)
    if addr:
        parsed = parse_addresses(f'"{addr.group(1).strip().strip(chr(34))}" <{addr.group(2)}>')
        out["author"] = parsed[0] if parsed else None
    m = re.match(r"On (.+?)(?:,? [^,]*?<[^>]+>)?\s*wrote:", h)
    if m:
        date_part = re.sub(r"\s(?:at)\s", " ", m.group(1))
        t = TIME_TOKEN.search(date_part)
        date_part = date_part[:t.end()] if t else re.split(r",? (?=[A-Z][a-z]+ [A-Z][a-z]+ <|\S+@)", date_part)[0]
        out["date_raw"] = date_part.strip(" ,")
        try:
            dt = dtparser.parse(out["date_raw"], fuzzy=True)
            out["date_parsed_local"] = dt.isoformat()
            out["has_offset"] = dt.tzinfo is not None
        except Exception:
            pass
    return out


def parse_forward_block(block: str) -> dict:
    hdr = {}
    for key in ("From", "Date", "Subject", "To", "Cc"):
        m = re.search(rf"^{key}:\s*(.+(?:\n(?![A-Z][a-z]+:)(?!\n).+)*)", block, re.M)
        if m:
            hdr[key.lower()] = " ".join(m.group(1).split())
    body_start = re.search(r"\n\s*\n", block[block.find("From:"):] if "From:" in block else block)
    return {"headers": hdr, "body_offset": (block.find("From:") + body_start.end()) if body_start and "From:" in block else 0}


def contact_form_author(text: str) -> Optional[dict]:
    """Website contact forms relay mail through wordpress@...; the real person is inside the body."""
    text = strip_link_artifacts(text)
    if not CONTACT_FORM.search(text):
        return None
    name = re.search(r"^From:\s*(.+)$", text, re.M)
    mail = re.search(r"^Email:\s*(\S+@\S+)", text, re.M)
    if not mail:
        return None
    parsed = parse_addresses(f'"{name.group(1).strip() if name else ""}" <{mail.group(1)}>')
    return parsed[0] if parsed else None


class EmailParser:
    def __init__(self, store):
        self.store = store

    # ------------------------------------------------------------- entry points
    def parse_eml(self, source) -> list[Unit]:
        msg = email.message_from_bytes(self.store.raw_bytes(source.source_id), policy=policy.default)
        return self._parse_message(msg, source.source_id, None)

    def parse_mbox(self, source) -> list[Unit]:
        mb = mailbox.mbox(source.meta["raw_store_path"],
                          factory=lambda f: email.message_from_binary_file(f, policy=policy.default))
        units = []
        for i, msg in enumerate(mb):
            units += self._parse_message(msg, source.source_id, i)
        return units

    # ------------------------------------------------------------- core
    def _parse_message(self, msg: EmailMessage, sid: str, idx: Optional[int]) -> list[Unit]:
        mid = normalize_message_id(msg.get("Message-ID"))
        utc, offset = email_date_to_utc(msg.get("Date"))
        subj = re.sub(r"\s+", " ", str(msg.get("Subject") or "")).strip()
        frm = parse_addresses(msg.get("From"))
        body, body_fmt = best_body(msg)
        parts = split_body(body)
        new_text, n_boiler = strip_boilerplate(body[parts["new"][0]:parts["new"][1]])
        refs = [normalize_message_id(r) for r in re.findall(r"<[^>]+>", str(msg.get("References") or ""))]
        unit_id = stable_id(sid, str(idx), mid or "", "email")
        flags = []
        if body_fmt == "html":
            flags.append("html_only_body")
        if mid is None:
            flags.append("missing_message_id")
        if offset is None:
            flags.append("missing_or_naive_date")
        if n_boiler:
            flags.append("boilerplate_removed")

        main = Unit(
            unit_id=unit_id, unit_type="email",
            locator=Locator(source_id=sid, kind="email_body", message_index=idx, message_id=mid,
                            char_start=parts["new"][0], char_end=parts["new"][1]),
            text=clean_text(new_text), raw_text=body,
            timestamp_utc=utc, timestamp_raw=str(msg.get("Date")),
            timestamp_confidence="exact" if offset else "none",
            author=frm[0] if frm else None,
            recipients=[dict(r, field=f) for f in ("To", "Cc", "Bcc") for r in parse_addresses(msg.get(f))],
            thread_key=str(msg.get("X-GM-THRID") or (refs[0] if refs else None) or subject_key(subj)),
            meta={"subject": subj, "subject_key": subject_key(subj),
                  "in_reply_to": normalize_message_id(msg.get("In-Reply-To")), "references": refs,
                  "gmail_labels": [l.strip() for l in str(msg.get("X-Gmail-Labels") or "").split(",") if l.strip()],
                  "original_utc_offset": offset, "body_format": body_fmt,
                  "boundary_kind": parts["boundary_kind"],
                  "signature_span": parts["signature"], "attachments": []},
            flags=flags)
        units = [main]

        relay_author = contact_form_author(new_text)
        if relay_author:
            main.meta["relayed_by"] = main.author
            main.author = relay_author
            main.flags.append("author_from_contact_form")

        if parts["rest"]:
            units += self._embedded(body, parts["rest"][0], sid, idx, mid, main, depth=1)

        for att in msg.iter_attachments():
            units += self._attachment(att, sid, idx, mid, main)
        for part in msg.walk():                       # inline calendar invites are not "attachments"
            if part.get_content_type() == "text/calendar" and "calendar" not in main.meta:
                main.meta["calendar"] = self._calendar(part.get_content())
        return units

    def _embedded(self, body: str, start: int, sid, idx, mid, parent: Unit, depth: int) -> list[Unit]:
        """Turn quoted / forwarded history into separate, clearly-labelled units (max 4 levels)."""
        if depth > 4 or start >= len(body):
            return []
        segment = body[start:]
        fwd = FORWARD_HEADER.match(segment)
        rep = REPLY_HEADER.match(segment)
        out_hdr, text_start, utype = {}, 0, "quoted_email"
        if fwd:
            info = parse_forward_block(segment[fwd.end():])
            out_hdr = info["headers"]; utype = "forwarded_email"
            text_start = fwd.end() + info["body_offset"]
            author = parse_addresses(out_hdr.get("from", ""))
            when_raw = out_hdr.get("date")
        else:
            hdr_end = rep.end() if rep else 0
            attr = parse_attribution(segment[:hdr_end]) if rep else {}
            author = [attr["author"]] if attr.get("author") else []
            when_raw = attr.get("date_raw")
            text_start = hdr_end
        inner_raw = segment[text_start:]
        inner = unquote(inner_raw)
        nested = split_body(inner)
        own, _ = strip_boilerplate(inner[:nested["rest"][0]] if nested["rest"] else inner)   # signatures kept here
        when_local, utc = None, None
        if when_raw:
            try:
                dt = dtparser.parse(re.sub(r"\bat\b", " ", when_raw), fuzzy=True)
                when_local = dt.isoformat()
                if dt.tzinfo is not None:
                    utc = email_date_to_utc(dt.strftime("%a, %d %b %Y %H:%M:%S %z"))[0]
            except Exception:
                pass
        u = Unit(
            unit_id=stable_id(parent.unit_id, "embedded", str(depth), str(start)),
            unit_type=utype,
            locator=Locator(source_id=sid, kind="quoted_fragment", message_index=idx, message_id=mid,
                            char_start=start, char_end=len(body)),
            text=clean_text(own), raw_text=segment,
            timestamp_utc=utc, timestamp_raw=when_raw,
            timestamp_confidence="exact" if utc else ("assumed_tz" if when_local else "none"),
            author=author[0] if author else None,
            thread_key=parent.thread_key,
            meta={"embedded_in": parent.unit_id, "depth": depth, "date_parsed_local_no_tz": None if utc else when_local,
                  "forward_headers": out_hdr or None, "subject_key": subject_key(out_hdr.get("subject", "")) or parent.meta["subject_key"]},
            flags=["reconstructed_from_quote" if utype == "quoted_email" else "forwarded_copy"])
        relay = contact_form_author(inner)
        if relay:
            u.meta["relayed_by"] = u.author; u.author = relay; u.flags.append("author_from_contact_form")
        out = [u] if u.text else []
        if nested["rest"]:
            # map nested offset back: approximate by searching the next boundary in the original segment
            nxt = [m.start() for rx in (REPLY_HEADER, FORWARD_HEADER) for m in [rx.search(body, start + text_start + 1)] if m]
            if nxt:
                out += self._embedded(body, min(nxt), sid, idx, mid, parent, depth + 1)
        return out

    def _attachment(self, att, sid, idx, mid, parent: Unit) -> list[Unit]:
        data = att.get_payload(decode=True) or b""
        name = att.get_filename() or f"attachment-{len(parent.meta['attachments'])}"
        child, is_new = self.store.add_bytes(data, name, f"{sid}#msg{idx}/{name}", parent_unit_id=parent.unit_id)
        parent.meta["attachments"].append({"filename": name, "content_type": att.get_content_type(),
                                           "source_id": child.source_id, "size": len(data),
                                           "already_seen": not is_new})
        return []          # the child file is parsed by the pipeline like any other source

    @staticmethod
    def _calendar(ics: str) -> dict:
        get = lambda k: (re.search(rf"^{k}[^:\n]*:(.+)$", ics, re.M) or [None, None])[1]
        return {"summary": get("SUMMARY"), "dtstart": get("DTSTART"), "dtend": get("DTEND"),
                "organizer": get("ORGANIZER"), "method": get("METHOD"), "status": get("STATUS")}
