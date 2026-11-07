"""Open a PDF and get, for each page, either its embedded text or a rendered image.

PyMuPDF is preferred (fast, good Vietnamese text extraction, and it renders pages for OCR);
pypdf is a text-only fallback so the text path still works if PyMuPDF is missing.
"""

from __future__ import annotations

from pathlib import Path

try:                                   # the modern name; `fitz` is the deprecated alias
    import pymupdf
except ImportError:                    # pragma: no cover
    try:
        import fitz as pymupdf
    except ImportError:
        pymupdf = None


class PdfDoc:
    """A thin wrapper exposing per-page text and per-page PNG rendering."""

    def __init__(self, path):
        self.path = str(path)
        self._fitz = None
        self._pypdf = None
        if pymupdf is not None:
            self._fitz = pymupdf.open(self.path)
        else:
            from pypdf import PdfReader
            self._pypdf = PdfReader(self.path)

    @property
    def n_pages(self) -> int:
        return self._fitz.page_count if self._fitz else len(self._pypdf.pages)

    def page_text(self, i: int) -> str:
        """Embedded text of page `i` (0-based). Empty string for an image-only page."""
        if self._fitz:
            try:
                return self._fitz[i].get_text("text") or ""
            except Exception:                                   # noqa: BLE001
                return ""
        try:
            return self._pypdf.pages[i].extract_text() or ""
        except Exception:                                       # noqa: BLE001
            return ""

    def page_texts(self) -> list:
        return [self.page_text(i) for i in range(self.n_pages)]

    def render_png(self, i: int, dpi: int = 200) -> bytes:
        """Render page `i` (0-based) to PNG bytes, for OCR. Needs PyMuPDF."""
        if not self._fitz:
            raise RuntimeError(
                "rendering pages for OCR requires PyMuPDF (pip install pymupdf); "
                "pypdf cannot rasterize")
        pix = self._fitz[i].get_pixmap(dpi=dpi)
        return pix.tobytes("png")

    def close(self):
        if self._fitz:
            self._fitz.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def have_pymupdf() -> bool:
    return pymupdf is not None
