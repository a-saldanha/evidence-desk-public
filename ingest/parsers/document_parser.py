"""PDFs and images -> one `doc_page` Unit per page.

Backends
  * LlamaParse v2 (`llama-cloud` SDK) is the primary parser: good tables, OCR, per-page confidence.
  * The PDF's own text layer (poppler `pdftotext`) is used as an independent witness. Comparing the two
    catches pages where the parser dropped or invented numbers, which matters because amounts, dates and
    booking codes are exactly what the knowledge graph will cite.

Results are cached by (file hash, tier, version) so re-runs cost zero credits.
"""
from __future__ import annotations
import json, os, re, shutil, subprocess, time
from pathlib import Path
from typing import Optional
from ..models import Unit, Locator, stable_id
from ..normalize import clean_text

# Cheap first-pass hints only; the LLM classifier in layer 2 replaces this.
DOC_TYPE_CUES = {
    "flight_ticket": ["electronic ticket receipt", "booking ref", "ticket number", "fare basis"],
    "payment_invoice": ["payment invoice", "grand total", "transaction id", "payment details"],
    "appointment_letter": ["appointment letter", "appointment details", "appointment date"],
    "invitation_letter": ["invitation letter", "we invite you"],
    "boarding_pass": ["boarding pass", "gate", "boarding time"],
    "bank_statement": ["statement of account", "opening balance", "closing balance"],
    "visa_application_form": ["application for schengen visa", "harmonised application form", "travel document", "purpose(s) of the journey"],
    "document_checklist": ["missing documents", "pending", "documents for business travel", "proof of accommodation"],
    "certificate": ["this certifies that", "certificate", "attended", "general chair"],
    "travel_itinerary": ["itinerary", "day 1", "boarding closes", "check out", "arrive"],
}
# Wording that marks a document as a plan / assumption rather than a record of what happened.
PLAN_MARKERS = re.compile(r"\b(assumed|verify|tbd|to be confirmed|tentative|forecast|planned|proposed|draft|"
                          r"you didn't specify|double-?check|re-check|may prefer)\b", re.I)
PAGE_X_OF_Y = re.compile(r"(?:^|\s)(?:page\s*)?(\d{1,3})\s*(?:/|of)\s*(\d{1,3})\s*$", re.I | re.M)
NUMERIC_TOKEN = re.compile(r"\b(?=[A-Z0-9]*\d)[A-Z0-9][A-Z0-9./:-]{3,}\b")


def native_pages(pdf_path: str) -> Optional[list[str]]:
    if not shutil.which("pdftotext"):
        return None
    out = subprocess.run(["pdftotext", "-layout", pdf_path, "-"], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    pages = out.stdout.split("\f")
    return pages[:-1] if pages and not pages[-1].strip() else pages


def page_x_of_y(text: str) -> Optional[tuple[int, int]]:
    hits = [(int(a), int(b)) for a, b in PAGE_X_OF_Y.findall(text) if 0 < int(a) <= int(b) < 500]
    return hits[-1] if hits else None


def has_text_layer(pdf_path: str) -> Optional[bool]:
    if not shutil.which("pdffonts"):
        return None
    out = subprocess.run(["pdffonts", pdf_path], capture_output=True, text=True).stdout.strip().splitlines()
    return len(out) > 2


def classify_page(text: str) -> tuple[Optional[str], int]:
    low = text.lower()
    scores = {k: sum(c in low for c in cues) for k, cues in DOC_TYPE_CUES.items()}
    best = max(scores, key=scores.get)
    return (best, scores[best]) if scores[best] >= 2 else (None, 0)


def numeric_agreement(reference: str, candidate: str) -> dict:
    """Share of ID-like / numeric tokens in the native text layer that survive into the parsed output."""
    ref = set(NUMERIC_TOKEN.findall(reference.upper()))
    cand_norm = re.sub(r"[\s,]", "", candidate.upper())
    if not ref:
        return {"tokens": 0, "recall": None, "missing": []}
    missing = sorted(t for t in ref if re.sub(r"[\s,]", "", t) not in cand_norm)
    return {"tokens": len(ref), "recall": round(1 - len(missing) / len(ref), 3), "missing": missing[:15]}


class DocumentParser:
    def __init__(self, store, cache_dir: Path, offline: bool = False,
                 version: str = "latest", text_tier: str = "cost_effective", scan_tier: str = "agentic"):
        self.store, self.cache = store, Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.version, self.text_tier, self.scan_tier = version, text_tier, scan_tier
        self.api_key = os.environ.get("LLAMA_CLOUD_API_KEY")
        self.offline = offline or not self.api_key
        self._last_parse_error: Optional[str] = None

    # ------------------------------------------------------------ LlamaParse
    def _llamaparse(self, path: str, sid: str, tier: str) -> Optional[dict]:
        self._last_parse_error = None
        key = self.cache / f"{sid}_{tier}_{self.version}.json"
        if key.exists():
            try:
                return json.loads(key.read_text())
            except ValueError:
                key.unlink()                      # a crash mid-write left a broken cache file: re-parse
        if self.offline:
            return None
        res = None
        for attempt in range(3):                  # transient timeout / 429 / 5xx, then give up gracefully
            try:
                from llama_cloud import LlamaCloud
                client = LlamaCloud(api_key=self.api_key)
                res = client.parsing.parse(upload_file=path, tier=tier, version=self.version,
                                           expand=["markdown", "text", "metadata"])
                break
            except Exception as e:                # a parse failure must never crash the whole ingest run
                self._last_parse_error = f"{type(e).__name__}: {str(e)[:120]}"
                if attempt == 2:
                    return None                   # caller falls back to the native PDF text layer
                time.sleep(2 ** attempt)
        pages = {}
        for p in (res.markdown.pages if res.markdown else []):
            pages.setdefault(p.page_number, {})
            if getattr(p, "success", True) and hasattr(p, "markdown"):
                pages[p.page_number].update(markdown=p.markdown, header=getattr(p, "header", None),
                                            footer=getattr(p, "footer", None))
            else:
                pages[p.page_number].update(error=getattr(p, "error", "page failed"))
        for p in (res.text.pages if res.text else []):
            pages.setdefault(p.page_number, {})["text"] = p.text
        for p in (res.metadata.pages if res.metadata and res.metadata.pages else []):
            pages.setdefault(p.page_number, {}).update(confidence=p.confidence,
                                                       rotation=p.original_orientation_angle)
        data = {"tier": tier, "version": self.version, "job_id": res.job.id,
                "credits": getattr(getattr(res.job, "usage", None), "credits", None),
                "document_confidence": getattr(getattr(res.metadata, "document", None), "confidence", None),
                "pages": [dict(page=k, **v) for k, v in sorted(pages.items())]}
        key.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        return data

    # ------------------------------------------------------------ main
    def parse(self, source) -> list[Unit]:
        path, sid = source.meta["raw_store_path"], source.source_id
        is_pdf = source.detected_type == "pdf"
        text_layer = has_text_layer(path) if is_pdf else False
        native = native_pages(path) if is_pdf and text_layer else None
        # A text layer can exist yet hold almost nothing (certificates, badges and scans with a stamped name).
        sparse = bool(native) and min(len(p.strip()) for p in native) < 80
        tier = self.text_tier if text_layer and not sparse else self.scan_tier
        lp = self._llamaparse(path, sid, tier)
        source.meta["document"] = {"text_layer": text_layer, "sparse_text_layer": sparse, "tier": tier if lp else None,
                                   "backend": "llamaparse" if lp else ("native_text_layer" if native else "none"),
                                   "llamaparse_doc_confidence": lp.get("document_confidence") if lp else None}
        if lp is None and self._last_parse_error:
            source.meta["document"]["parse_error"] = self._last_parse_error
        if lp is None and native is None:
            source.meta["document"]["needs_parser"] = True
            source.meta["skipped"] = (f"LlamaParse failed ({self._last_parse_error}); no text layer to fall back to"
                                      if self._last_parse_error
                                      else "no LlamaParse (offline / no LLAMA_CLOUD_API_KEY) and no PDF text layer")
            return []

        n_pages = max(len(native or []), len(lp["pages"]) if lp else 0)
        units, prev_type, segment = [], None, 0
        for i in range(n_pages):
            nat = (native[i] if native and i < len(native) else "") or ""
            lpp = (lp["pages"][i] if lp and i < len(lp["pages"]) else {}) or {}
            primary = lpp.get("markdown") or lpp.get("text") or nat
            dtype, strength = classify_page(primary or nat)
            pxy = page_x_of_y(nat or primary)
            restart = bool(units) and pxy is not None and pxy[0] == 1      # "1 / 1" after "3 / 3" = new document
            if units and ((dtype and dtype != prev_type) or restart):
                segment += 1
            if not dtype and not restart:
                dtype = prev_type                       # continuation page inherits the previous type
            prev_type = dtype or prev_type
            flags, meta = [], {"doc_type_hint": dtype, "segment": segment,
                               "backend": "llamaparse" if lpp.get("markdown") else "native_text_layer",
                               "llamaparse_confidence": lpp.get("confidence"),
                               "page_header": lpp.get("header"), "page_footer": lpp.get("footer")}
            if lpp.get("error"):
                flags.append("llamaparse_page_failed")
            if lpp.get("markdown") and nat.strip():
                meta["cross_check"] = numeric_agreement(nat, lpp["markdown"])
            if len(clean_text(primary)) < 40:
                flags.append("near_empty_page" if lpp.get("markdown") else "image_only_page_needs_ocr")
            plan_hits = PLAN_MARKERS.findall(primary or nat)
            if plan_hits:
                meta["plan_markers"] = sorted({h.lower() for h in plan_hits})
            meta["page_x_of_y"] = pxy
            units.append(Unit(
                unit_id=stable_id(sid, "page", str(i + 1)), unit_type="doc_page",
                locator=Locator(source_id=sid, kind="pdf_page" if is_pdf else "image", page=i + 1),
                text=clean_text(primary), raw_text=nat or primary,
                meta=meta, flags=flags))
        return units
