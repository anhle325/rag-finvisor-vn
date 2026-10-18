"""titles.classify() turns a source's free-text title into structured metadata.

These are the examples the parser was designed against (real titles captured from
Vietstock and CafeF, Sept 2026), so they double as documentation of the rules.
"""

import pytest

from bctc_collector.models import DocRecord
from bctc_collector.titles import canonical_name, classify, period_str


def rec(title, source="vietstock", **kw):
    return classify(DocRecord(source=source, ticker=kw.pop("ticker", "VNM"),
                              title=title, url=kw.pop("url", ""), **kw))


@pytest.mark.parametrize("title,year,ptype,quarter,scope,audit", [
    ("BCTC Hợp nhất Soát xét 6 tháng đầu năm 2025", 2025, "H1", 6, "hopnhat", "reviewed"),
    ("BCTC Công ty mẹ Kiểm toán năm 2024", 2024, "FY", 0, "rieng", "audited"),
    ("BCTC quý 3 năm 2025", 2025, "Q", 3, "standalone", "unaudited"),
    ("Báo cáo tài chính hợp nhất quý 2 năm 2026", 2026, "Q", 2, "hopnhat", "unaudited"),
])
def test_classify_core_fields(title, year, ptype, quarter, scope, audit):
    r = rec(title)
    assert (r.year, r.period_type, r.quarter, r.scope, r.audit) == \
           (year, ptype, quarter, scope, audit)
    assert r.parse_ok


def test_quarter_in_roman_numerals():
    assert rec("BCTC Hợp nhất quý IV năm 2024").quarter == 4


def test_cafef_reviewed_q2_is_half_year():
    # CafeF files the reviewed half-year report as "quý 2 ... (đã soát xét)";
    # it is the same document Vietstock calls "Soát xét 6 tháng đầu năm".
    r = rec("Báo cáo tài chính quý 2 năm 2025 (đã soát xét)", source="cafef")
    assert (r.period_type, r.quarter) == ("H1", 6)


def test_partial_statement_is_not_a_full_report():
    assert rec("CDKT Hợp nhất quý 4 năm 2025").doc_kind == "partial"


def test_restatement_marker_detected():
    assert rec("BCTC Hợp nhất quý 4 năm 2025 (điều chỉnh)").restated is True


def test_unparsable_title_marked_not_ok():
    r = rec("Tài liệu đính kèm")
    assert r.parse_ok is False
    assert "could not determine" in r.parse_note


def test_canonical_name_and_period_str():
    r = rec("BCTC Hợp nhất Kiểm toán năm 2024")
    r.ticker = "VNM"
    assert canonical_name(r) == "VNM_BCTC_HN_N2024"
    assert period_str(2024, "Q", 3) == "2024Q3"
    assert period_str(2025, "H1", 6) == "2025H1"
