"""Deterministic normalizers. No LLM here: these must be cheap, repeatable and testable."""
from __future__ import annotations
import re, unicodedata
from datetime import datetime, timezone, timedelta
from email.utils import getaddresses, parsedate_to_datetime
from typing import Optional

# ---------------------------------------------------------------- text
# WhatsApp and Outlook sprinkle invisible direction marks and odd spaces everywhere.
_INVISIBLE = dict.fromkeys(map(ord, "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff\u200b"), None)
_ODD_SPACES = {ord(c): " " for c in "\u00a0\u202f\u2007\u2009"}


def strip_link_artifacts(s: str) -> str:
    """HTML->text conversions leave 'name@x.com<mailto:name@x.com>' and 'site.org<http://site.org/>'."""
    s = re.sub(r"<mailto:[^>\s]+>", "", s)
    def _link(m):
        label, url = m.group(1), m.group(2)
        return label if url.rstrip("/").lower().endswith(label.rstrip("/").lower()) else f"{label} ({url})"
    return re.sub(r"(\S+)<(https?://[^>\s]+)>", _link, s)


def clean_text(s: str) -> str:
    s = strip_link_artifacts(s)
    s = unicodedata.normalize("NFC", s).translate(_INVISIBLE).translate(_ODD_SPACES)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# ---------------------------------------------------------------- identities
def canonical_email(addr: str) -> str:
    """Lower-case; for Gmail drop dots and +tags (jane.smith == janesmith)."""
    addr = (addr or "").strip().strip("<>").lower()
    if "@" not in addr:
        return addr
    local, domain = addr.rsplit("@", 1)
    if domain in ("gmail.com", "googlemail.com"):
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def parse_addresses(*header_values) -> list[dict]:
    out = []
    for name, addr in getaddresses([str(v) for v in header_values if v]):
        if not addr:
            continue
        name = re.sub(r"\s*\(via [^)]*\)", "", name).strip().strip('"')   # "X (via Google Slides)"
        if "@" in name:                                                      # display name is just an address
            name = ""
        out.append({"display": name or None, "address": addr.lower(), "canonical": canonical_email(addr)})
    return out


def normalize_message_id(mid: Optional[str]) -> Optional[str]:
    if not mid:
        return None
    m = re.search(r"<([^>]+)>", str(mid))
    return (m.group(1) if m else str(mid)).strip().lower()


# ---------------------------------------------------------------- dates
def email_date_to_utc(value) -> tuple[Optional[str], Optional[str]]:
    """Returns (utc_iso, original_offset) for an RFC 2822 Date header."""
    if not value:
        return None, None
    try:
        dt = value.datetime if hasattr(value, "datetime") else parsedate_to_datetime(str(value))
    except Exception:
        return None, None
    if dt.tzinfo is None:
        return dt.isoformat(), None
    return dt.astimezone(timezone.utc).isoformat(), dt.strftime("%z")


def infer_day_first(date_strings: list[str], sep: str = "/") -> Optional[bool]:
    """Look across a whole file: any first field > 12 proves DD/MM, any second field > 12 proves MM/DD."""
    first_gt12 = second_gt12 = False
    for s in date_strings:
        parts = re.split(r"[./-]", s)
        if len(parts) < 2 or not (parts[0].isdigit() and parts[1].isdigit()):
            continue
        first_gt12 |= int(parts[0]) > 12
        second_gt12 |= int(parts[1]) > 12
    if first_gt12 and not second_gt12:
        return True
    if second_gt12 and not first_gt12:
        return False
    return None   # undecidable (or contradictory) -> caller must flag


def local_to_utc(dt: datetime, tz_offset_hours: float) -> str:
    return (dt - timedelta(hours=tz_offset_hours)).replace(tzinfo=timezone.utc).isoformat()


# Date-looking strings inside documents, tagged with the convention they imply.
DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b"), "dash_numeric"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b"), "slash_numeric"),
    (re.compile(r"\b\d{1,2}\s?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s?\d{4}\b", re.I), "named_month"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "iso"),
]


def find_dates(text: str) -> list[dict]:
    hits = []
    for rx, kind in DATE_PATTERNS:
        for m in rx.finditer(text):
            h = {"raw": m.group(0), "kind": kind, "start": m.start(), "end": m.end(), "order": "unambiguous"}
            if kind in ("dash_numeric", "slash_numeric"):
                a, b = int(m.group(1)), int(m.group(2))
                h["order"] = "day_first" if a > 12 else "month_first" if b > 12 else "ambiguous"
            hits.append(h)
    return hits


# ---------------------------------------------------------------- money
_CUR_SYMBOL = {"$": "USD?", "€": "EUR", "£": "GBP", "₹": "INR", "Rs.": "INR", "Rs": "INR"}
_CODES = "INR|USD|EUR|GBP|QAR|AED|JPY|CHF|CAD|AUD|SGD"
MONEY_RX = re.compile(
    rf"(?P<code1>\b(?:{_CODES})\b)\s?(?P<amt1>\d[\d,]*(?:\.\d+)?)(?P<suffix>[A-Z0-9]{{2}}\b)?"
    rf"|(?P<sym>[$€£₹]|Rs\.?)\s?(?P<amt2>\d[\d,]*(?:\.\d+)?)"
    rf"|(?P<amt3>\d[\d,]*(?:\.\d+)?)\s?(?P<code2>\b(?:{_CODES})\b)"
)


def find_money(text: str) -> list[dict]:
    """Keeps the raw string. '$' is recorded as 'USD?' because it is genuinely ambiguous (USD/CAD/AUD/SGD)."""
    out = []
    for m in MONEY_RX.finditer(text):
        code = m.group("code1") or m.group("code2") or _CUR_SYMBOL.get(m.group("sym") or "", None)
        amt = m.group("amt1") or m.group("amt2") or m.group("amt3")
        try:
            value = float(amt.replace(",", ""))
        except (TypeError, ValueError):
            continue
        out.append({"raw": m.group(0), "currency": code, "amount": value,
                    "tax_code": m.group("suffix"), "start": m.start(), "end": m.end()})
    return out


# ---------------------------------------------------------------- sensitive data
SENSITIVE_PATTERNS = {
    "password": re.compile(r"\b(?:password|passphrase|passcode|pwd|pin)\b\s*(?:for [^:\n]{0,40})?\s*(?:is|:|=)\s*\S+", re.I),
    "passport_number": re.compile(r"\b[A-Z]\d{7}\b"),        # only reported when a passport label is nearby
    "phone_number": re.compile(r"(?<!\w)\+\d{1,3}[\s-]?\d[\d\s-]{7,13}\d\b"),
    "card_number": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "api_key": re.compile(r"\b(?:sk|llx|pk)-[A-Za-z0-9_\-]{16,}\b"),
}


def _luhn(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()][::-1]
    return len(d) >= 13 and sum(x if i % 2 == 0 else (x * 2 - 9 if x * 2 > 9 else x * 2) for i, x in enumerate(d)) % 10 == 0


RELAY_ADDRESS = re.compile(r"^(?:no-?reply|do-?not-?reply|notifications?|wordpress|mailer-daemon|drive-shares[^@]*|calendar-notification)@", re.I)


def is_relay(address: str | None) -> bool:
    return bool(address and RELAY_ADDRESS.match(address))


def find_sensitive(text: str) -> list[dict]:
    hits = []
    for kind, rx in SENSITIVE_PATTERNS.items():
        for m in rx.finditer(text):
            if kind == "card_number" and not _luhn(m.group(0)):
                continue
            if kind == "passport_number" and not re.search(r"passport|travel\s+document|number of travel", text[max(0, m.start() - 600):m.start()], re.I):
                continue
            hits.append({"kind": kind, "start": m.start(), "end": m.end(), "preview": m.group(0)[:6] + "…"})
    # labelled values in forms sit in columns, so look for the first date after the label rather than on the same line
    for m in re.finditer(r"date of birth", text, re.I):
        d = re.compile(r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{4}\b").search(text, m.end(), m.end() + 400)
        if d:
            hits.append({"kind": "date_of_birth", "start": d.start(), "end": d.end(), "preview": d.group(0)[:3] + "…"})
    return hits
