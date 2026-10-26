"""Core records. Everything downstream (extraction, graph, citations) points back here.

SourceFile  one physical file (or an attachment pulled out of an email), content-addressed.
Unit        the smallest citable piece: one email, one chat message, one PDF page.
QCIssue     anything a human or a later layer should know about the data's quality.
"""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def stable_id(*parts: str, n: int = 16) -> str:
    return hashlib.sha256("||".join(parts).encode()).hexdigest()[:n]


@dataclass
class SourceFile:
    source_id: str                 # sha256 of bytes -> identical files collapse automatically
    filename: str
    path: str                      # where the raw bytes live (never modified)
    detected_type: str             # email | mbox | whatsapp | pdf | image | calendar | unknown
    size: int
    parent_unit_id: Optional[str] = None   # set when extracted from an email attachment
    aliases: list[str] = field(default_factory=list)  # other filenames/paths with identical bytes
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Locator:
    """Enough to re-open the original and highlight the exact place."""
    source_id: str
    kind: str                      # email_body | chat_line | pdf_page | quoted_fragment | attachment
    message_index: Optional[int] = None    # position inside an mbox
    message_id: Optional[str] = None
    line_start: Optional[int] = None       # 1-based, for chat exports
    line_end: Optional[int] = None
    page: Optional[int] = None             # 1-based, for PDFs
    char_start: Optional[int] = None       # offsets into the decoded body / page text
    char_end: Optional[int] = None


@dataclass
class Unit:
    unit_id: str
    unit_type: str                 # email | chat_message | chat_event | doc_page | quoted_email
    locator: Locator
    text: str                      # cleaned text that goes to the LLM
    raw_text: str                  # untouched decoded text for the same locator
    timestamp_utc: Optional[str] = None
    timestamp_raw: Optional[str] = None
    timestamp_confidence: str = "none"     # exact | assumed_tz | ambiguous | none
    author: Optional[dict] = None          # {"display": .., "address": .., "canonical": ..}
    recipients: list[dict] = field(default_factory=list)
    thread_key: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)


SEVERITIES = ("info", "warning", "error")


@dataclass
class QCIssue:
    check: str                     # machine name, e.g. "missing_thread_parent"
    severity: str
    message: str
    source_ids: list[str] = field(default_factory=list)
    unit_ids: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    suggested_action: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)
