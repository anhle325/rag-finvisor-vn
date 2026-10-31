"""Decide, per page, whether the embedded text is usable or the page must be OCR'd.

"Has text" is not the same as "has usable Vietnamese text": a scanned filing often carries a
thin text layer (a digital-signature stamp, or a scanner's accent-less OCR) that fools a
naive character count. So a page counts as usable only if it has enough characters AND those
characters look like real Vietnamese (a minimum share of diacritics).

This mirrors, in miniature, the probe the crawler already runs. If the crawler's catalog is
available the caller can trust its `kind`/`text_quality` instead — but routing here keeps the
ingestion step self-contained and able to run on any folder of PDFs.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[^\W\d_]+", re.U)
_VI_DIACRITICS = set("àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữự"
                     "ỳýỷỹỵđÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢ"
                     "ÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ")


def diacritic_ratio(text: str) -> float:
    """Share of words containing at least one Vietnamese diacritic (0..1)."""
    words = _WORD.findall(text or "")
    if not words:
        return 0.0
    hit = sum(1 for w in words if any(ch in _VI_DIACRITICS for ch in w))
    return hit / len(words)


def page_is_textual(text: str, min_chars: int = 120, min_diacritic: float = 0.12) -> bool:
    """True if `text` is a usable Vietnamese text layer, not a scan's thin residue."""
    t = (text or "").strip()
    if len(t) < min_chars:
        return False
    return diacritic_ratio(t) >= min_diacritic


def route_pages(page_texts, min_chars=120, min_diacritic=0.12,
                force=None) -> list:
    """Return a list of 'text' / 'ocr', one per page.

    force = 'text'  -> never OCR (use the embedded layer, whatever it is)
    force = 'ocr'   -> OCR every page
    force = None    -> decide per page with page_is_textual()
    """
    if force == "text":
        return ["text"] * len(page_texts)
    if force == "ocr":
        return ["ocr"] * len(page_texts)
    return ["text" if page_is_textual(t, min_chars, min_diacritic) else "ocr"
            for t in page_texts]


def doc_method(page_methods) -> str:
    """Summarise per-page decisions into a document-level label."""
    s = set(page_methods)
    if s == {"text"}:
        return "text"
    if s == {"ocr"}:
        return "ocr"
    return "mixed"
