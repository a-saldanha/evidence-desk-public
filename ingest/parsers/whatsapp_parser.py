"""WhatsApp `_chat.txt` exports (iOS `[dd/mm/yy, h:mm:ss AM] Name: msg` and Android `dd/mm/yy, hh:mm - Name: msg`).

Problems handled:
  * day/month order is locale dependent -> inferred from the whole file, flagged if undecidable
  * no timezone in the export -> configurable default, every unit marked `assumed_tz`
  * multi-line messages, invisible U+200E marks, narrow no-break spaces before AM/PM
  * system lines, calls, deleted/edited messages, '<media> omitted' placeholders (= missing evidence)
"""
from __future__ import annotations
import re
from datetime import datetime
from ..models import Unit, Locator, stable_id
from ..normalize import clean_text, infer_day_first, local_to_utc

LINE_IOS = re.compile(r"^\[(?P<d>\d{1,2}[./-]\d{1,2}[./-]\d{2,4}),? (?P<t>\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:\s?[APap]\.?\s?[Mm]\.?)?)\] (?P<rest>.*)$")
LINE_ANDROID = re.compile(r"^(?P<d>\d{1,2}[./-]\d{1,2}[./-]\d{2,4}),? (?P<t>\d{1,2}[:.]\d{2}(?:\s?[APap]\.?\s?[Mm]\.?)?) [-–] (?P<rest>.*)$")
SENDER = re.compile(r"^(?P<who>[^:\n]{1,80}?)\s?:(?: (?P<msg>.*))?$", re.S)   # msg may be empty (album header)

MEDIA = re.compile(r"(?:(?P<fname>.+?)\s*•\s*(?P<pages>\d+) pages?\s*)?\b(?P<kind>image|video|audio|sticker|GIF|document|Contact card) omitted\s*$"
                   r"|^<(?P<kind2>Media) omitted>\s*$|^(?P<fname2>.+?\.\w{2,5}) \(file attached\)\s*$", re.I)
ATTACHED = re.compile(r"<attached: (?P<fname>[^>]+)>")
CALL = re.compile(r"^(?P<missed>Missed )?(?P<kind>Voice|Video|Group voice|Group video) call(?:,\s*(?P<detail>.+))?$", re.I)
SYSTEM_HINTS = ("end-to-end encrypted", "disappearing messages", "changed the subject", "created group",
                "changed this group", "security code changed", "joined using this group",
                "uses a default timer", "you turned off", "you turned on", "changed their phone number",
                "created this group", "added you", "is a contact", "removed you", "changed the group",
                "pinned a message", "you're now an admin", "this business uses",
                # common non-English exports
                "ende-zu-ende-verschlüsselt", "cifrados de extremo a extremo", "chiffrés de bout en bout",
                "criptografia de ponta a ponta", "crittografati end-to-end")


def _strip(s: str) -> str:
    return s.replace("\u200e", "").replace("\u202f", " ").replace("\u00a0", " ")


class WhatsAppParser:
    def __init__(self, store, default_tz_hours: float = 5.5, self_aliases: dict | None = None):
        """default_tz_hours: exports carry no zone; 5.5 = IST. self_aliases maps display names to a person id."""
        self.store, self.tz, self.self_aliases = store, default_tz_hours, self_aliases or {}

    def parse(self, source) -> list[Unit]:
        text = self.store.raw_bytes(source.source_id).decode("utf-8", "replace")
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

        # 1. group physical lines into logical messages (continuation lines have no timestamp prefix)
        msgs, cur = [], None
        for no, raw in enumerate(lines, start=1):
            line = _strip(raw)
            m = LINE_IOS.match(line.lstrip()) or LINE_ANDROID.match(line.lstrip())
            if m:
                cur = {"d": m["d"], "t": m["t"], "rest": m["rest"], "start": no, "end": no, "raw": [raw]}
                msgs.append(cur)
            elif cur is not None:
                cur["rest"] += "\n" + line; cur["end"] = no; cur["raw"].append(raw)

        day_first = infer_day_first([m["d"] for m in msgs])
        if day_first is None and msgs and all("." in m["d"] for m in msgs):
            day_first = True                      # dd.mm.yy is the only convention that uses dots
        source.meta["whatsapp"] = {"day_first": day_first, "assumed_tz_hours": self.tz, "messages": len(msgs),
                                   "participants": sorted({SENDER.match(m["rest"])["who"].strip()
                                                           for m in msgs if SENDER.match(m["rest"])})}
        units = []
        for i, m in enumerate(msgs):
            units.append(self._unit(source, i, m, day_first))
        return units

    def _unit(self, source, i, m, day_first) -> Unit:
        sid = source.source_id
        ts_utc, conf, local_iso = None, "none", None
        try:
            dnorm = re.sub(r"[.-]", "/", m["d"])
            fmt_d = ("%d/%m/" if day_first in (True, None) else "%m/%d/") + ("%y" if len(dnorm.split("/")[-1]) == 2 else "%Y")
            t = m["t"].upper().replace(" ", "")
            t = re.sub(r"(?<=\d)\.(?=\d)", ":", t).replace(".", "")
            fmt_t = "%I:%M:%S%p" if t.count(":") == 2 and t[-2:] in ("AM", "PM") else \
                    "%I:%M%p" if t[-2:] in ("AM", "PM") else "%H:%M:%S" if t.count(":") == 2 else "%H:%M"
            local = datetime.strptime(f"{dnorm} {t}", f"{fmt_d} {fmt_t}")
            local_iso = local.isoformat()
            ts_utc = local_to_utc(local, self.tz)
            conf = "ambiguous" if day_first is None else "assumed_tz"
        except ValueError:
            pass

        sm = SENDER.match(m["rest"])
        who, body = (sm["who"].strip(), sm["msg"] or "") if sm else (None, m["rest"])
        body_clean = clean_text(body)
        flags, meta, utype = [], {"local_time": local_iso, "line_count": m["end"] - m["start"] + 1}, "chat_message"

        low = body_clean.lower()
        if who is None or any(h in low for h in SYSTEM_HINTS) and len(body_clean) < 250:
            utype = "chat_event"; flags.append("system_message")
        c = CALL.match(body_clean)
        if c:
            utype = "chat_event"; meta["call"] = {"kind": c["kind"].lower(), "missed": bool(c["missed"]), "detail": c["detail"]}
            flags.append("call_log")
        if not body_clean:
            flags.append("empty_message")            # usually the header line of a photo album
        md, at = MEDIA.search(body_clean), ATTACHED.search(body_clean)
        if md and md["fname2"]:                       # Android export *with* media: "IMG-1.jpg (file attached)"
            meta["media"] = {"kind": "attached", "filename": md["fname2"].strip(), "pages": None}
            flags.append("media_attached")
        elif md:
            meta["media"] = {"kind": (md["kind"] or md["kind2"]).lower(), "filename": (md["fname"] or "").strip() or None,
                             "pages": int(md["pages"]) if md["pages"] else None}
            flags.append("media_missing_from_export")
        elif at:
            meta["media"] = {"kind": "attached", "filename": at["fname"], "pages": None}
            flags.append("media_attached")
        if "This message was deleted" in body_clean or "You deleted this message" in body_clean:
            flags.append("deleted_message")
        if "<This message was edited>" in body_clean:
            flags.append("edited_message"); body_clean = body_clean.replace("<This message was edited>", "").strip()

        return Unit(
            unit_id=stable_id(sid, "wa", str(m["start"])),
            unit_type=utype,
            locator=Locator(source_id=sid, kind="chat_line", line_start=m["start"], line_end=m["end"]),
            text=body_clean, raw_text="\n".join(m["raw"]),
            timestamp_utc=ts_utc, timestamp_raw=f"{m['d']} {m['t']}", timestamp_confidence=conf,
            author={"display": who, "address": None, "canonical": self.self_aliases.get(who)} if who else None,
            thread_key=f"whatsapp:{sid[:12]}", meta=meta, flags=flags)
