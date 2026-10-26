"""Content-addressed raw store. Files are identified by their bytes, never by their names
(our sample 'visa_appointement.pdf' also contains a payment invoice, and 'Your_Electronic_Receipt.pdf'
is a flight ticket), and exact duplicates collapse to one SourceFile with aliases."""
from __future__ import annotations
import json, re, shutil
from pathlib import Path
from typing import Optional
from .models import SourceFile, sha256_bytes

# dd/mm/yy, mm/dd/yy, dd.mm.yy, dd-mm-yyyy; 12h or 24h; iOS "[...] " or Android " - "
WA_IOS = re.compile(rb"^\W{0,3}\[\d{1,2}[./-]\d{1,2}[./-]\d{2,4},? \d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:(?:\s|\xe2\x80\xaf)?[AaPp]\.?\s?[Mm]\.?)?\] ", re.M)
WA_ANDROID = re.compile(rb"^\W{0,3}\d{1,2}[./-]\d{1,2}[./-]\d{2,4},? \d{1,2}[:.]\d{2}(?:(?:\s|\xe2\x80\xaf)?[AaPp]\.?\s?[Mm]\.?)? [-\xe2\x80\x93] ", re.M)


def sniff(data: bytes, filename: str) -> str:
    head = data[:4096]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head[:3] == b"\xff\xd8\xff" or head.startswith(b"\x89PNG") or head[:4] in (b"RIFF", b"GIF8") or b"ftypheic" in head[:32]:
        return "image"
    if head.startswith(b"BEGIN:VCALENDAR"):
        return "calendar"
    if head.startswith(b"From ") and re.search(rb"^From: ", data[:20000], re.M):
        return "mbox"
    if re.search(rb"^(Received|From|Message-ID|MIME-Version|Date|Subject):", head, re.M | re.I) and b"\n\n" in data.replace(b"\r\n", b"\n")[:200000]:
        return "email"
    if len(WA_IOS.findall(data[:20000])) >= 3 or len(WA_ANDROID.findall(data[:20000])) >= 3:
        return "whatsapp"
    if filename.lower().endswith(".msg") and head.startswith(b"\xd0\xcf\x11\xe0"):
        return "outlook_msg"          # needs `extract-msg`; routed but not parsed in this demo
    if head.startswith(b"PK\x03\x04"):
        names = data[:200000]
        if b"word/document.xml" in names:
            return "docx"
        if b"xl/workbook.xml" in names:
            return "xlsx"
        if b"ppt/presentation.xml" in names:
            return "pptx"
        return "zip"
    if b"<smses" in head or b"<allsms" in head:
        return "sms_backup"           # Android "SMS Backup & Restore" XML
    if b"\x00" not in head:
        try:
            data[:200000].decode("utf-8")
            return "text" if data.strip() else "empty"
        except UnicodeDecodeError:
            try:
                data[:200000].decode("utf-16")
                return "text"
            except UnicodeDecodeError:
                pass
    return "unknown"


class SourceStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "raw").mkdir(parents=True, exist_ok=True)
        self.sources: dict[str, SourceFile] = {}

    def add_bytes(self, data: bytes, filename: str, origin: str,
                  parent_unit_id: Optional[str] = None, type_hint: Optional[str] = None) -> tuple[SourceFile, bool]:
        """Returns (source, is_new). Identical bytes from a second origin just add an alias."""
        sid = sha256_bytes(data)
        if sid in self.sources:
            src = self.sources[sid]
            if origin not in src.aliases and origin != src.path:
                src.aliases.append(origin)
            if parent_unit_id and not src.parent_unit_id:
                src.parent_unit_id = parent_unit_id
            return src, False
        raw_path = self.root / "raw" / f"{sid[:2]}" / sid
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(data)
        src = SourceFile(source_id=sid, filename=filename, path=origin,
                         detected_type=type_hint or sniff(data, filename), size=len(data),
                         parent_unit_id=parent_unit_id, meta={"raw_store_path": str(raw_path)})
        self.sources[sid] = src
        return src, True

    def add_path(self, p: Path) -> tuple[SourceFile, bool]:
        return self.add_bytes(p.read_bytes(), p.name, str(p))

    def raw_bytes(self, sid: str) -> bytes:
        return Path(self.sources[sid].meta["raw_store_path"]).read_bytes()

    def dump(self, path: Path):
        path.write_text(json.dumps([s.__dict__ for s in self.sources.values()], indent=2, ensure_ascii=False))
