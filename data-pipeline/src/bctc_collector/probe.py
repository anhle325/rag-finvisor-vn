"""Look inside a PDF before letting it anywhere near an index.

N01's probe asked one question — "is there a text layer?" — and on the synthetic
pack the answer split the corpus cleanly into 137 + 8. Real Vietnamese filings
need four more questions, because "has text" is not "has usable text":

| kind / text_quality | what it looks like in pypdf                   | typical cause                       |
|---------------------|-----------------------------------------------|-------------------------------------|
| scanned             | '' on every page                              | printed, signed, scanned            |
| mixed               | text on some pages only                       | digital cover + scanned statements  |
| no_diacritics       | 'BAO cAo TAI CHiNH HOP NHAT'                  | scanner's built-in OCR layer        |
| legacy_font         | 'B¸o c¸o tµi chÝnh'                           | TCVN3/VNI fonts from old Word files |
| cid_garbage         | '(cid:123)(cid:88)'                           | fonts with no Unicode map           |
| english             | 'CONSOLIDATED FINANCIAL STATEMENTS'           | the English version                 |

Each needs a different fix (OCR, font remap, re-OCR, language routing), so the
probe keeps the evidence — the ratios — not just the verdict.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .titles import strip_accents

RE_WORD = re.compile(r"[^\W\d_]+", re.U)
# characters that almost only appear when TCVN3 (ABC) bytes are read as Latin-1/cp1252
_TCVN3_RAW = "¸µ¶·¹¨©ª«¬®¯°±²³´½¾ÆÇÌÎÏÑÒÓÔÕÖ×ØÝÞßåæçèéêëìíîïñò"
EN_STOP = {"the", "of", "and", "for", "to", "in", "as", "at", "on", "by", "with", "from",
           "statement", "statements", "financial", "consolidated", "year", "ended"}
VI_LETTERS = set("àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ")
# ...minus every character that is also a legitimate Vietnamese letter (é, ì, ò, Ô ...)
TCVN3_CHARS = {c for c in _TCVN3_RAW if c.lower() not in VI_LETTERS}

# matched on accent-stripped lowercase text, so OCR output without diacritics works too
# same line only ([ \t], not \s): "các đơn vị\n tỷ đồng" in a cover letter is not a unit
RE_UNIT = re.compile(r"(?:don vi(?: tinh)?|dvt)[ \t]*[:\-][ \t]*"
                     r"(trieu dong|nghin dong|ngan dong|ty dong|dong viet nam|trieu vnd|"
                     r"nghin vnd|vnd|dong)\b")
STATEMENT_MARKERS = ("ma so", "thuyet minh", "tong cong tai san", "nguon von")
UNIT_MAP = {"trieu": "trieu", "nghin": "nghin", "ngan": "nghin", "ty": "ty"}
LETTER_MARKERS = ("cong bo thong tin", "v/v", "giai trinh", "kinh gui",
                  "cong hoa xa hoi chu nghia")
FORM_HINTS = (("b02/tctd", "bank"), ("b01/tctd", "bank"), ("tctd", "bank"),
              ("b01-ctck", "securities"), ("b02-ctck", "securities"),
              ("b01-dnpnt", "insurance"), ("b01-dnbh", "insurance"),
              ("b01-dn", "corp"), ("b01a-dn", "corp"), ("b02-dn", "corp"))


def text_quality(text: str) -> dict:
    """Ratios that tell usable Vietnamese text from its look-alikes."""
    text = unicodedata.normalize("NFC", text or "")
    words = [w.lower() for w in RE_WORD.findall(text)]
    n = len(words)
    if n == 0:
        return dict(words=0, diacritic_ratio=0.0, tcvn3_ratio=0.0, cid_per_1k=0.0,
                    en_ratio=0.0, verdict="empty")
    dia = sum(1 for w in words if any(ch in VI_LETTERS for ch in w)) / n
    chars = [ch for ch in text if not ch.isspace()]
    tc = sum(1 for ch in chars if ch in TCVN3_CHARS) / max(1, len(chars))
    cid = text.count("(cid:") * 1000 / max(1, len(text))
    en = sum(1 for w in words if w in EN_STOP) / n
    if cid > 5:
        verdict = "cid_garbage"
    elif tc > 0.02:
        verdict = "legacy_font"
    elif dia >= 0.25:
        verdict = "ok"
    elif en >= 0.08:
        verdict = "english"
    else:
        verdict = "no_diacritics"
    return dict(words=n, diacritic_ratio=round(dia, 3), tcvn3_ratio=round(tc, 4),
                cid_per_1k=round(cid, 2), en_ratio=round(en, 3), verdict=verdict)


# a column header repeats the unit once per value column: "VND  VND", "Triệu đồng  Triệu đồng"
# (plain "đồng" twice is not enough: "Hội đồng ... cổ đông" would match)
RE_HEADER_UNIT = re.compile(r"\b(?:(trieu|nghin|ngan|ty)\s+)?vnd[\s|_`'.:-]{1,60}(?:\1\s+)?vnd\b"
                            r"|\b(trieu|nghin|ngan|ty)\s+dong[\s|_`'.:-]{1,60}\2\s+dong\b")
# only blanks / table rules between the two: "(1.355,78 tỷ đồng) ... tỷ đồng" in a
# cover letter is prose, not a column header (GVR Q1/2026)


def find_unit(text):
    """'Đơn vị tính: triệu đồng' — or, as most real statements do it, a column header
    'Mã số  Thuyết minh   VND   VND' (VNM 2025, after OCR)."""
    t = strip_accents(unicodedata.normalize("NFC", text or "")).lower()
    m = RE_UNIT.search(t)
    if not m:
        h = RE_HEADER_UNIT.search(t)          # re.S: pypdf often puts each header on its own line
        return UNIT_MAP.get(h.group(1) or h.group(2) or "", "dong") if h else None
    u = m.group(1)
    for k, v in UNIT_MAP.items():
        if u.startswith(k):
            return v
    return "dong"


def find_form(text):
    t = strip_accents((text or "").lower()).replace(" ", "")
    for key, form in FORM_HINTS:
        if strip_accents(key).replace(" ", "") in t:
            return form
    return None


def strip_furniture(texts, min_share=0.5):
    """Remove lines that repeat on most pages (headers, footers, e-signature stamps).

    Real example (GAS, every page of a scanned report): "Văn bản này được xác thực
    tại https://cks.pvgas.com.vn với mã tài liệu: KVN.24.1918" — ~115 characters of
    text per page on top of a picture. Counted naively, the scan looks 'digital'."""
    n = len(texts)
    if n < 4:
        return texts
    def key(ln):
        return re.sub(r"[\d\W_]+", "", ln.lower())      # ignore page numbers / punctuation
    freq = Counter()
    for t in texts:
        freq.update({key(ln) for ln in t.splitlines() if key(ln)})
    common = {k for k, c in freq.items() if c >= min_share * n and len(k) >= 6}
    if not common:
        return texts
    return ["\n".join(ln for ln in t.splitlines() if key(ln) not in common) for t in texts]


def probe_pdf(path, min_cpp=200, max_quality_pages=12):
    """Open once, return every fact the catalog needs. Never raises on a bad PDF."""
    from pypdf import PdfReader

    out = dict(pages=0, chars=0, chars_per_page=0.0, pages_with_text=0, kind="broken",
               text_quality="n/a", encrypted=False, signed=False, unit=None,
               statement_form=None, cover_text="", cover_letter=False, producer="",
               error="")
    try:
        raw = open(path, "rb").read()
        out["signed"] = b"/ByteRange" in raw or b"/Type /Sig" in raw or b"/Type/Sig" in raw
        reader = PdfReader(path)
        out["encrypted"] = bool(reader.is_encrypted)
        try:
            meta = reader.metadata or {}
            prod = " / ".join(str(meta.get(k, "")).strip() for k in
                              ("/Producer", "/Creator") if meta.get(k))
            # PDF metadata may hold NUL / control bytes, which break CSV readers
            out["producer"] = re.sub(r"[\x00-\x1f\x7f]", " ", prod)[:120].strip()
        except Exception:                                       # noqa: BLE001
            pass
        if reader.is_encrypted:
            try:
                reader.decrypt("")          # most "encrypted" filings only forbid printing
            except Exception:                                   # noqa: BLE001
                pass
        texts = []
        for p in reader.pages:
            try:
                texts.append(p.extract_text() or "")
            except Exception:                                   # noqa: BLE001
                texts.append("")
    except Exception as e:                                      # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"[:200]
        return out

    n = len(texts)
    texts = strip_furniture(texts)
    per_page = [len(t.strip()) for t in texts]
    with_text = sum(1 for c in per_page if c >= 50)
    out.update(pages=n, chars=sum(per_page),
               chars_per_page=round(sum(per_page) / n, 1) if n else 0.0,
               pages_with_text=with_text)
    if n == 0:
        return out
    share = with_text / n
    out["kind"] = ("scanned" if share < 0.2 else "mixed" if share < 0.8 else
                   "digital" if out["chars_per_page"] >= min_cpp else "mixed")
    # judge the text that exists: for a scan this is often the OCR layer of the
    # disclosure cover letter ("Công văn CBTT") the company put in front of the report
    text_pages = [t for t in texts if len(t.strip()) >= 50]
    sample = "\n".join(text_pages[:max_quality_pages])
    q = text_quality(sample)
    out["text_quality"] = q["verdict"] if text_pages else "n/a"
    out.update({f"q_{k}": v for k, v in q.items() if k != "verdict"})
    # the unit sits on the statement pages, which real filings put after a cover
    # letter, the management report and the audit opinion — search everything
    # statement pages first: the explanation letter in front often speaks "tỷ đồng"
    # (GVR Q1/2026) while the statements themselves are in VND
    stmt = [t for t in texts if any(m in re.sub(r"\s+", " ", strip_accents(t).lower())
                                    for m in STATEMENT_MARKERS)]
    out["unit"] = find_unit("\n".join(stmt)) or find_unit("\n".join(texts))
    out["statement_form"] = find_form(sample)
    out["cover_text"] = "\n".join(texts[:3])[:4000]
    first = strip_accents(texts[0]).lower() if texts else ""
    out["cover_letter"] = sum(m in first for m in LETTER_MARKERS) >= 2
    return out


def verify_cover(cover_text, ticker, year, scope):
    """Does the document say what the source claimed? Returns 'ok', 'unverifiable'
    or 'mismatch:<fields>'. Only checks what the cover can prove."""
    t = strip_accents(unicodedata.normalize("NFC", cover_text or "")).lower()
    if len(t.strip()) < 40:
        return "unverifiable"
    bad = []
    if str(year) not in t:
        bad.append("year")
    if scope == "hopnhat" and "hop nhat" not in t and "consolidated" not in t:
        bad.append("scope")
    if scope == "rieng" and ("hop nhat" in t and "rieng" not in t and "cong ty me" not in t):
        bad.append("scope")
    return "ok" if not bad else "mismatch:" + ",".join(bad)
