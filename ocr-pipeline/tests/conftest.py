"""Shared fixtures. All tests run offline and need no OCR engine (PaddleOCR is optional)."""

import pytest

pymupdf = pytest.importorskip("pymupdf")


@pytest.fixture
def text_pdf(tmp_path):
    """A small 3-page PDF with a real text layer, named like a real report."""
    doc = pymupdf.open()
    body = ("Bao cao tai chinh hop nhat nam 2025. Don vi tinh trieu dong. "
            "Tong cong tai san va nguon von duoc trinh bay day du. ") * 8
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"Trang {i + 1}\n\n{body}\n\nMa so 270 Tong cong tai san.",
                         fontsize=11, fontname="helv")
    path = tmp_path / "VNM_BCTC_HN_N2025.pdf"
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def meta():
    return dict(ticker="VNM", year=2025, quarter=0, period="2025", scope="hopnhat",
                audited=True, unit="trieu", exchange="HOSE")
