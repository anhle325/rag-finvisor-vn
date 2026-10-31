"""Routing (text vs OCR) and text cleaning."""

from bctc_ingest.clean import normalize, strip_furniture
from bctc_ingest.route import diacritic_ratio, doc_method, page_is_textual, route_pages


def test_diacritic_ratio():
    assert diacritic_ratio("Báo cáo tài chính hợp nhất") > 0.5
    assert diacritic_ratio("BAO CAO TAI CHINH HOP NHAT") == 0.0   # scanner OCR, no accents
    assert diacritic_ratio("") == 0.0


def test_page_is_textual_needs_length_and_diacritics():
    good = "Báo cáo tài chính hợp nhất của công ty cho năm tài chính 2025. " * 4
    assert page_is_textual(good)
    assert not page_is_textual("short")                           # too short
    assert not page_is_textual("THIS IS A LONG ENGLISH OR ACCENTLESS LINE " * 5)  # no diacritics


def test_route_and_force():
    pages = ["Báo cáo tài chính hợp nhất năm 2025 trình bày đầy đủ số liệu. " * 4, ""]
    assert route_pages(pages) == ["text", "ocr"]
    assert route_pages(pages, force="ocr") == ["ocr", "ocr"]
    assert route_pages(pages, force="text") == ["text", "text"]


def test_doc_method():
    assert doc_method(["text", "text"]) == "text"
    assert doc_method(["ocr", "ocr"]) == "ocr"
    assert doc_method(["text", "ocr"]) == "mixed"


def test_normalize_collapses_whitespace():
    assert normalize("a  b\n\n\n\nc") == "a b\n\nc"


def test_strip_furniture_removes_repeated_header_and_signature():
    header = "CONG TY CO PHAN ABC - BAO CAO"
    bodies = ["tong tai san tang", "loi nhuan rong giam", "dong tien hoat dong on dinh",
              "no vay dai han giam", "von chu so huu tang truong"]
    pages = [f"{header}\n{bodies[i]}\nDigitally signed by ABC" for i in range(5)]
    cleaned = strip_furniture(pages)
    assert all("Digitally signed" not in p for p in cleaned)       # signature line gone
    assert all(header not in p for p in cleaned)                   # repeated header gone
    assert all(bodies[i] in cleaned[i] for i in range(5))          # distinct content kept
