"""Engine-agnostic OCR evaluation for Vietnamese financial statements.

Two kinds of evidence, because a real corpus has no transcripts:

1. **Digital twins** — pages of a born-digital filing, rendered to an image and degraded
   like a scan. The PDF's own text layer is the exact ground truth, so we can compute
   character error rate (CER), word F1 and — what a financial RAG really lives on —
   **number F1**.
2. **Real scans** — no ground truth, but accounting does not lie: on a balance sheet
   (Thông tư 200) 100 = 110+120+130+140+150, 200 = 210+…+260, 270 = 100+200,
   300 = 310+330, 440 = 300+400 and 270 = 440, in BOTH value columns. One misread digit
   breaks at least one identity. Plus: total assets vs the reference table.

Every function takes plain text, so any engine (Tesseract, PaddleOCR, a VLM emitting
markdown tables) is judged by the same rules.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .titles import strip_accents

NUM = re.compile(r"\(?-?\d{1,3}(?:[.,]\d{3})+\)?")          # 1.234.567 / (1,234) / 12.345
BIGNUM = re.compile(r"\(?-?\d{1,3}(?:[.,]\d{3}){2,}\)?")      # >= 7 digits: statement values
CODE = re.compile(r"(?<![\d.,/])([1-4]\d{2}[a-z]?)(?![\d.,/])")

# Thông tư 200/2014 balance sheet (B01-DN): parent = sum(children)
TT200_IDENTITIES = {
    "100": ["110", "120", "130", "140", "150"],
    "200": ["210", "220", "230", "240", "250", "260"],
    "270": ["100", "200"],
    "300": ["310", "330"],
    "440": ["300", "400"],
}
TT200_TOTAL = "270"

# Thông tư 99/2025 (from reporting year 2026): 'Tài sản dài hạn khác' becomes 270 and
# total assets moves to 280 = 100 + 200 (seen on POW / HPG Q1-2026 filings).
TT99_IDENTITIES = {
    "100": ["110", "120", "130", "140", "150", "160"],
    "200": ["210", "220", "230", "240", "250", "260", "270"],
    "280": ["100", "200"],
    "300": ["310", "330"],
    "440": ["300", "400"],
}
TT99_TOTAL = "280"


# ------------------------------------------------------------------ normalise
def normalize(text):
    """NFC, drop markdown/table furniture, collapse whitespace."""
    t = unicodedata.normalize("NFC", text or "")
    t = re.sub(r"<[^>]+>", " ", t)                    # html tags from VLM table output
    t = re.sub(r"[|*#`_~>]+", " ", t)
    t = re.sub(r"-{3,}|={3,}", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def to_int(s):
    neg = s.startswith("(") or s.startswith("-")
    v = int(re.sub(r"[^\d]", "", s) or 0)
    return -v if neg else v


# ------------------------------------------------------------------- metrics
def levenshtein(a, b):
    try:
        from rapidfuzz.distance import Levenshtein
        return Levenshtein.distance(a, b)
    except ImportError:
        pass
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(pred, gt):
    p, g = normalize(pred), normalize(gt)
    return levenshtein(p, g) / max(1, len(g))


def _f1(pred_items, gt_items):
    pc, gc = Counter(pred_items), Counter(gt_items)
    tp = sum((pc & gc).values())
    prec = tp / max(1, sum(pc.values()))
    rec = tp / max(1, sum(gc.values()))
    return (2 * prec * rec / (prec + rec)) if prec + rec else 0.0, prec, rec


def words(text):
    return re.findall(r"[^\W\d_]+", normalize(text).lower())


def word_f1(pred, gt):
    """Order-free: a VLM that emits a table in a different reading order is not punished."""
    return _f1(words(pred), words(gt))[0]


def diacritic_word_f1(pred, gt):
    """Only the words that carry a Vietnamese diacritic — where Latin-only OCR fails."""
    keep = lambda ws: [w for w in ws if strip_accents(w) != w]
    return _f1(keep(words(pred)), keep(words(gt)))[0]


def numbers(text):
    return [to_int(m) for m in NUM.findall(normalize(text))]


def number_f1(pred, gt):
    f, p, r = _f1(numbers(pred), numbers(gt))
    return f, p, r


# ------------------------------------------------------- balance-sheet parsing
def balance_rows(text):
    """{code: [value_current, value_prior]} from any OCR text of a B01 page.

    A row is a line (or a markdown table row) holding a 3-digit code and at least one
    statement-sized number AFTER it. Codes printed in the left margin, a 'Thuyết minh'
    reference column, '(270 = 100 + 200)' formulas — all handled."""
    rows = {}
    for ln in (text or "").splitlines():
        ln2 = re.sub(r"\(\s*\d{3}\s*=[^)]*\)", " ", ln)            # formula hints
        for m in CODE.finditer(ln2):
            vals = BIGNUM.findall(ln2[m.end():])
            if vals:
                code = m.group(1)[:3]
                rows.setdefault(code, [to_int(v) for v in vals[:2]])
                break
    return rows


# Parents whose children are all mandatory lines of B01: check only when EVERY child is on
# the page (270 = 100 + 200 often spans two pages — 100 on page 1, 200 and 270 on page 2).
ALL_CHILDREN_REQUIRED = {"270", "280", "300", "440"}


def detect_accounting(text, rows=None):
    """'tt99' if the page shows total assets under code 280, else 'tt200'."""
    rows = balance_rows(text) if rows is None else rows
    if re.search(r"280\s*=\s*100", text or ""):
        return "tt99"
    if "280" in rows and "270" in rows and rows["280"][0] > 2 * abs(rows["270"][0]):
        return "tt99"
    return "tt200"


def identity_checks(rows, identities=TT200_IDENTITIES, total=TT200_TOTAL):
    """Returns (passed, checked, details).

    An identity is only checked when the page holds enough of it to be judged:
    270/300/440 need all children; 100/200 need at least two children (optional lines
    such as 120 'Đầu tư tài chính ngắn hạn' may be absent = 0). Otherwise it is skipped,
    never counted as a failure — a missing component is a page break, not an OCR error."""
    passed = checked = 0
    details = []
    for parent, kids in identities.items():
        if parent not in rows:
            continue
        present = [k for k in kids if k in rows]
        need = len(kids) if parent in ALL_CHILDREN_REQUIRED else 2
        if len(present) < need:
            continue
        for col in (0, 1):
            if col >= len(rows[parent]):
                continue
            try:
                s = sum(rows[k][col] for k in present if col < len(rows[k]))
            except (IndexError, TypeError):
                continue
            ok = s == rows[parent][col]
            checked += 1
            passed += ok
            details.append((parent, col, ok))
    # total assets = total resources (270 = 440, or 280 = 440 under TT99)
    for col in (0, 1):
        if total in rows and "440" in rows and col < min(len(rows[total]), len(rows["440"])):
            ok = rows[total][col] == rows["440"][col]
            checked += 1
            passed += ok
            details.append((f"{total}=440", col, ok))
    return passed, checked, details


def totals(text):
    """(total assets, total resources) read by label — engine-agnostic."""
    from .ocr import RE_TA_LABEL, RE_TNV_LABEL, labeled_value
    return labeled_value(text, RE_TA_LABEL)[0], labeled_value(text, RE_TNV_LABEL)[0]


def evaluate_page(pred, gt=None, rows_gt=None, ta_ref=None, unit_mult=1, accounting=None):
    """One dict of metrics for one page and one engine. accounting: 'tt200' / 'tt99' /
    None = detect from the page (use the ground truth's form when there is one)."""
    out = {}
    ta, tnv = totals(pred)
    out.update(ta=ta, tnv=tnv)
    if ta_ref:
        vals = [v * unit_mult for v in (ta, tnv) if v]
        out["ta_matches_reference"] = any(abs(v - ta_ref) <= 0.0005 * ta_ref for v in vals)
    if gt is not None:
        out["cer"] = round(cer(pred, gt), 4)
        out["word_f1"] = round(word_f1(pred, gt), 4)
        out["diacritic_word_f1"] = round(diacritic_word_f1(pred, gt), 4)
        f, p, r = number_f1(pred, gt)
        out.update(number_f1=round(f, 4), number_precision=round(p, 4), number_recall=round(r, 4))
    rows = balance_rows(pred)
    acc = accounting or detect_accounting(gt if gt is not None else pred)
    ids, total = (TT99_IDENTITIES, TT99_TOTAL) if acc == "tt99" else (TT200_IDENTITIES, TT200_TOTAL)
    passed, checked, _ = identity_checks(rows, ids, total)
    out.update(bs_codes=len(rows), identities_passed=passed, identities_checked=checked)
    if rows_gt:
        common = [c for c in rows_gt if c in rows]
        out["bs_cells_right"] = sum(rows[c][:1] == rows_gt[c][:1] for c in common)
        out["bs_cells_gt"] = len(rows_gt)
    return out
