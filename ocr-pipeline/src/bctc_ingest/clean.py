"""Normalise extracted/OCR'd text before chunking.

Three cheap passes that matter a lot for Vietnamese financial PDFs:

1. Unicode NFC — Vietnamese can arrive decomposed (NFD); normalising makes the same word
   compare equal and embed consistently.
2. Whitespace — collapse runs of spaces/blank lines that both PyMuPDF and OCR produce.
3. Repeated furniture — headers, footers and the digital-signature stamp that repeat on
   most pages add noise to every chunk; drop lines that appear on a majority of pages.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

# Lines from the e-signature block that scanned filings stamp on the cover / every page.
_SIGNATURE_HINTS = ("digitally signed", "signature not verified", "ký bởi", "ký số",
                    "được xác thực", "mã tài liệu", "date:")


def normalize(text: str) -> str:
    """NFC + collapse whitespace, per line (keeps line breaks, which chunking uses)."""
    text = unicodedata.normalize("NFC", text or "")
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    # collapse 3+ blank lines to one
    out, blanks = [], 0
    for ln in lines:
        if ln:
            out.append(ln)
            blanks = 0
        else:
            blanks += 1
            if blanks == 1:
                out.append("")
    return "\n".join(out).strip()


def _furniture_key(line: str) -> str:
    return re.sub(r"[\d\W_]+", "", line.lower())


def strip_furniture(page_texts: list, min_share: float = 0.6) -> list:
    """Remove lines that repeat on >= `min_share` of pages (headers/footers/stamps).

    Also drops obvious e-signature lines on any page. Returns a new list of page texts.
    A document with fewer than 4 pages is left untouched (too little evidence)."""
    pages = list(page_texts)
    if len(pages) >= 4:
        freq = Counter()
        for t in pages:
            freq.update({_furniture_key(ln) for ln in t.splitlines() if _furniture_key(ln)})
        n = len(pages)
        common = {k for k, c in freq.items() if c >= min_share * n and len(k) >= 6}
    else:
        common = set()

    cleaned = []
    for t in pages:
        kept = []
        for ln in t.splitlines():
            low = ln.lower()
            if any(h in low for h in _SIGNATURE_HINTS):
                continue
            if _furniture_key(ln) in common:
                continue
            kept.append(ln)
        cleaned.append("\n".join(kept).strip())
    return cleaned
