"""Robustness: unfamiliar formats must be read or clearly reported, never silently dropped.

    python -m tests.test_unfamiliar_files
"""
import io, tempfile, zipfile
from pathlib import Path
from reason.workspace import Workspace
from reason.extract import build_windows

FILES = {
    "WhatsApp Chat with Priya.txt": "12/25/24, 9:15 PM - Priya: Viewing tomorrow at 11?\n12/25/24, 9:21 PM - Priya: <Media omitted>\n12/26/24, 1:05 PM - Rahul: Moved to Friday\n",
    "WhatsApp Chat mit Jonas.txt": "03.01.25, 14:06 - Jonas: Rechnung bezahlt? 450 €\n03.01.25, 14:30 - Anna: Ja\n04.01.25, 09:12 - Jonas: Danke!\n",
    "notes.md": "# Sync 14 March 2025\n- Launch moved to April 2\n",
    "page.html": "<html><body><h1>Booking confirmed</h1><p>Check-in 5 May 2025</p></body></html>",
    "sms.xml": '<?xml version="1.0"?><smses count="1"><sms address="+911" date="1740810000000" type="2" body="Reached airport" readable_date="x" contact_name="Dad" /></smses>',
    "empty.txt": "",
}


def docx_bytes(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f"<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>")
    return buf.getvalue()


def main():
    d = Path(tempfile.mkdtemp())
    src = d / "in"; src.mkdir()
    for n, t in FILES.items():
        (src / n).write_text(t, encoding="utf-8")
    (src / "offer.docx").write_bytes(docx_bytes("Offer: Data Analyst from 1 May 2025"))
    (src / "blob.bin").write_bytes(bytes(range(256)))
    ws = Workspace(d / "case", "Rahul Mehta", ["Rahul"], [], 5.5)
    ws.ingest([src], offline=True)
    types = {s["filename"]: s["detected_type"] for s in ws.sources().values()}
    expect = {"WhatsApp Chat with Priya.txt": "whatsapp", "WhatsApp Chat mit Jonas.txt": "whatsapp", "notes.md": "text",
              "page.html": "text", "sms.xml": "sms_backup", "offer.docx": "docx", "empty.txt": "empty", "blob.bin": "unknown"}
    assert types == expect, types
    issues = {i["check"]: i for i in ws.issues()}
    assert "blob.bin" in issues["unsupported_files"]["message"]
    assert "chat_media_missing" in issues
    assert not any("Jonas" in i["message"] for i in ws.issues() if i["check"] == "chat_date_order_ambiguous")
    html_unit = next(u for u in ws.units() if "Booking confirmed" in u["text"])
    assert "<h1>" not in html_unit["text"]
    kinds = sorted(w.kind for w in build_windows(ws.units(), ws.sources()))
    print("types ok;", "windows:", kinds)
    print("OK")


if __name__ == "__main__":
    main()
