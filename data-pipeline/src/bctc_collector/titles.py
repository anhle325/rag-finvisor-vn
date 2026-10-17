"""Turn a source's free-text title into structured metadata.

Real titles, as captured from the two aggregators (Sept 2026):

    Vietstock  "BCTC Hợp nhất Soát xét 6 tháng đầu năm 2025"
               "BCTC Công ty mẹ Kiểm toán năm 2024"
               "BCTC quý 3 năm 2025"                      (company with no subsidiary)
               "CDKT Hợp nhất quý 4 năm 2025 (điều chỉnh)"  (a PART of a report)
    CafeF      "Báo cáo tài chính hợp nhất quý 2 năm 2026"
               "Báo cáo tài chính công ty mẹ năm 2025 (đã kiểm toán)"
               "Báo cáo tài chính quý 2 năm 2025 (đã soát xét)"

The parser is deliberately conservative: when the title does not say something,
the field stays at its default and `parse_note` explains what was assumed.
"""

from __future__ import annotations

import re
import unicodedata

from .models import DocRecord


def norm(s: str) -> str:
    """NFC + lowercase + collapse whitespace."""
    s = unicodedata.normalize("NFC", s or "")
    return re.sub(r"\s+", " ", s).strip().lower()


def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s.replace("đ", "d").replace("Đ", "D")


RE_YEAR = re.compile(r"(?<!\d)(20\d{2}|19\d{2})(?!\d)")
RE_QUARTER = re.compile(r"\bquy\s*([1-4]|i{1,3}|iv)\b")
RE_H1 = re.compile(r"\b(6 thang|sau thang|ban nien|giua nien do)\b")
RE_9M = re.compile(r"\b9 thang\b")
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}

# Vietstock's short Title says "CDKT ...", its FullName says "Cân đối kế toán ..." —
# both are ONE statement of a report, not the report (observed: PVS Q2/2024)
PARTIAL_PREFIX = ("cdkt", "kqkd", "lctt", "bang can doi", "can doi ke toan",
                  "ket qua kinh doanh", "ket qua hoat dong kinh doanh",
                  "bao cao ket qua hoat dong", "luu chuyen tien te", "bao cao luu chuyen")
EXPLANATION_PREFIX = ("thuyet minh", "giai trinh", "cong van", "bao cao giai trinh")
RESTATED_MARKERS = ("dieu chinh", "dieu_chinh", "dieuchinh", "inh_chinh", "dinh chinh",
                    "sua doi", "bo sung", "restat")


def classify(rec: DocRecord) -> DocRecord:
    """Fill the structured fields of `rec` from its title, URL and API fields."""
    t = strip_accents(norm(rec.title))
    u = strip_accents(norm(rec.url))
    notes = []

    # --- year ----------------------------------------------------------------
    ys = RE_YEAR.findall(t)
    if ys:
        rec.year = int(ys[-1])
    elif rec.api_year:
        rec.year = int(rec.api_year)
        notes.append("year from API field")

    # --- period ---------------------------------------------------------------
    mq = RE_QUARTER.search(t)
    if RE_9M.search(t):
        rec.period_type, rec.quarter = "9M", 9
    elif RE_H1.search(t):
        rec.period_type, rec.quarter = "H1", 6
    elif mq:
        q = mq.group(1)
        rec.period_type, rec.quarter = "Q", int(q) if q.isdigit() else ROMAN[q]
    elif re.search(r"\bnam\s+(19|20)\d{2}", t) or "thuong nien" in t:
        rec.period_type, rec.quarter = "FY", 0
    elif rec.api_quarter is not None:
        # CafeF: Quarter 1-4, 5 = annual ("CN" = cả năm)
        aq = int(rec.api_quarter)
        rec.period_type, rec.quarter = ("FY", 0) if aq in (0, 5) else ("Q", aq)
        notes.append("period from API field")

    # --- scope ----------------------------------------------------------------
    if "hop nhat" in t:
        rec.scope = "hopnhat"
    elif "cong ty me" in t or re.search(r"\brieng\b", t) or "van phong" in t:
        rec.scope = "rieng"
    else:
        # No scope word: the company publishes a single (standalone) report.
        rec.scope = "standalone"
        notes.append("no scope in title -> standalone")

    # --- audit status ------------------------------------------------------
    if "kiem toan" in t and "chua kiem toan" not in t:
        rec.audit = "audited"
    elif "soat xet" in t:
        rec.audit = "reviewed"
    else:
        rec.audit = "unaudited"
        if rec.period_type == "FY":
            notes.append("annual report with no audit word")

    # --- kind, language, restatement ------------------------------------------
    if t.startswith(PARTIAL_PREFIX):
        rec.doc_kind = "partial"
    elif t.startswith(EXPLANATION_PREFIX):
        rec.doc_kind = "explanation"
    else:
        rec.doc_kind = "full"
    if "tieng anh" in t or "english" in t or "/en/" in u:
        rec.lang = "en"
    rec.restated = any(m in t or m in u for m in RESTATED_MARKERS)

    # CafeF files the reviewed half-year report as "quý 2 năm Y (đã soát xét)";
    # Vietstock calls the same document "Soát xét 6 tháng đầu năm Y".
    if (rec.source == "cafef" and rec.period_type == "Q" and rec.quarter == 2
            and rec.audit == "reviewed"):
        rec.period_type, rec.quarter = "H1", 6
        notes.append("cafef 'quý 2 (đã soát xét)' -> H1")

    rec.parse_ok = rec.year is not None and rec.period_type != ""
    if not rec.parse_ok:
        notes.append("could not determine year/period")
    rec.parse_note = "; ".join(notes)
    return rec


def period_str(year, period_type, quarter) -> str:
    """Course convention: '2024Q1', '2024' for annual, plus '2024H1' / '2024M9'."""
    if period_type == "Q":
        return f"{year}Q{quarter}"
    if period_type == "H1":
        return f"{year}H1"
    if period_type == "9M":
        return f"{year}M9"
    return f"{year}"


DEFAULT_AUDIT = {"Q": "unaudited", "H1": "reviewed", "9M": "reviewed", "FY": "audited"}


def canonical_name(rec, version: int = 1) -> str:
    """File name in the course's first naming convention: TICKER_BCTC_HN_Q1_2024.

    Suffixes appear only when needed to keep identities apart:
    _SX / _KT / _CKT when the audit status is not the default for that period,
    _v2.. for later versions, _EN for English, _PART / _TM for partial docs.
    """
    sc = {"hopnhat": "HN", "rieng": "RIENG", "standalone": "DL"}.get(rec.scope, "XX")
    pt = rec.period_type
    per = {"Q": f"Q{rec.quarter}_{rec.year}", "H1": f"6T_{rec.year}",
           "9M": f"9T_{rec.year}", "FY": f"N{rec.year}"}.get(pt, f"X{rec.year}")
    name = f"{rec.ticker}_BCTC_{sc}_{per}"
    if rec.audit != DEFAULT_AUDIT.get(pt):
        name += {"audited": "_KT", "reviewed": "_SX", "unaudited": "_CKT"}[rec.audit]
    if rec.doc_kind == "partial":
        name += "_PART"
    elif rec.doc_kind == "explanation":
        name += "_TM"
    if rec.lang == "en":
        name += "_EN"
    if version > 1:
        name += f"_v{version}"
    return name
