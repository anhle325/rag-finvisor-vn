"""A thin PaddleOCR wrapper: render a PDF page to an image, OCR it, return (text, confidence).

Targets **PaddleOCR 3.x** (the current API), with a fallback to the 2.x API if an older
version is installed.

PaddleOCR 3.x differs from 2.x:

* constructor: no ``show_log``; ``use_angle_cls`` is now ``use_textline_orientation``; the
  optional document pre-processing steps are toggled with ``use_doc_orientation_classify`` /
  ``use_doc_unwarping``; small CPU models are selected by name (``PP-OCRv5_mobile_det`` /
  ``PP-OCRv5_mobile_rec``).
* inference: ``ocr.ocr(img, cls=...)`` is replaced by ``ocr.predict(img)``.
* results: each item exposes parallel lists ``rec_texts`` / ``rec_scores`` / ``rec_polys``
  instead of 2.x's ``[[box, (text, score)], ...]``.

PaddleOCR is an **optional** dependency, imported lazily, so the rest of the pipeline runs
without it. Install (CPU):  pip install "paddlepaddle>=3.0" "paddleocr>=3.0"
"""

from __future__ import annotations

import io


def available() -> bool:
    """True if PaddleOCR can be imported in this environment."""
    try:
        import paddleocr  # noqa: F401
        return True
    except Exception:                                           # noqa: BLE001
        return False


# --------------------------------------------------------------------- parsing
def _poly_sort_key(poly):
    """Top-to-bottom, then left-to-right, from a polygon's corner points."""
    try:
        xs = [float(p[0]) for p in poly]
        ys = [float(p[1]) for p in poly]
        return (round(min(ys), 1), round(min(xs), 1))
    except Exception:                                           # noqa: BLE001
        return (0.0, 0.0)


def _fields_v3(res):
    """Pull (texts, scores, polys) out of one PaddleOCR 3.x result item.

    The item may be an OCRResult (dict-like), or expose the data under `.json['res']`."""
    data = None
    for getter in (lambda: res["rec_texts"] is not None and res,      # dict-like item
                   lambda: res.json.get("res", res.json)):            # .json wrapper
        try:
            candidate = getter()
            if candidate is not None and "rec_texts" in candidate:
                data = candidate
                break
        except Exception:                                       # noqa: BLE001
            continue
    if data is None:
        return [], [], []
    texts = list(data.get("rec_texts") or [])
    scores = list(data.get("rec_scores") or [])
    polys = list(data.get("rec_polys") or data.get("dt_polys") or [])
    return texts, scores, polys


def parse_predict(results):
    """Turn a 3.x `predict()` return value into (text, mean_confidence), reading order."""
    texts, confs = [], []
    for res in results or []:
        rtexts, rscores, rpolys = _fields_v3(res)
        rows = []
        for i, t in enumerate(rtexts):
            score = float(rscores[i]) if i < len(rscores) else 0.0
            poly = rpolys[i] if i < len(rpolys) else None
            rows.append((t, score, poly))
        rows.sort(key=lambda r: _poly_sort_key(r[2]) if r[2] is not None else (0.0, 0.0))
        for t, s, _ in rows:
            texts.append(t)
            confs.append(s)
    text = "\n".join(texts)
    mean = round(sum(confs) / len(confs), 4) if confs else 0.0
    return text, mean


def parse_ocr(result):
    """Turn a 2.x `ocr()` return value [[ [box,(text,score)], ... ]] into (text, mean_conf)."""
    lines = result[0] if result and result[0] else []

    def ypos(item):
        box = item[0]
        return (min(p[1] for p in box), min(p[0] for p in box))

    rows, confs = [], []
    for item in sorted(lines, key=ypos):
        rows.append(item[1][0])
        confs.append(float(item[1][1]))
    return "\n".join(rows), (round(sum(confs) / len(confs), 4) if confs else 0.0)


# ---------------------------------------------------------------------- engine
class PaddleOCREngine:
    """Lazily-constructed PaddleOCR, reused across pages (model load is expensive).

    Notes for PaddleOCR 3.x:

    * We pass ``lang`` and let PaddleOCR pick the matching recognition model (for Vietnamese
      that is the right small model). We do NOT hard-code model names — doing so makes
      PaddleOCR ignore ``lang`` and can select a non-Vietnamese recognizer. Override only via
      ``det_model_name`` / ``rec_model_name`` if you know what you want.
    * ``enable_mkldnn`` controls the oneDNN CPU backend. Some Windows CPU builds crash inside
      oneDNN under the new PIR executor (``NotImplementedError: ... onednn_instruction.cc``).
      If that happens, this engine automatically rebuilds with ``enable_mkldnn=False`` and
      retries once — set ``enable_mkldnn=False`` up front to skip the first failing attempt.
    """

    def __init__(self, lang: str = "vi", dpi: int = 200,
                 use_textline_orientation: bool = False, enable_mkldnn: bool = None,
                 device: str = None, det_model_name: str = None, rec_model_name: str = None):
        self.lang = lang
        self.dpi = dpi
        self.use_textline_orientation = use_textline_orientation
        self.enable_mkldnn = enable_mkldnn
        self.device = device
        self.det_model_name = det_model_name
        self.rec_model_name = rec_model_name
        self._ocr = None
        self._api = None                 # "v3" | "v2"

    def _build(self):
        from paddleocr import PaddleOCR

        # --- PaddleOCR 3.x: lang-driven model, preprocessing off ------------
        kw = dict(lang=self.lang,
                  use_doc_orientation_classify=False,
                  use_doc_unwarping=False,
                  use_textline_orientation=self.use_textline_orientation)
        if self.enable_mkldnn is not None:
            kw["enable_mkldnn"] = self.enable_mkldnn
        if self.device:
            kw["device"] = self.device
        if self.det_model_name:
            kw["text_detection_model_name"] = self.det_model_name
        if self.rec_model_name:
            kw["text_recognition_model_name"] = self.rec_model_name
        try:
            self._ocr = PaddleOCR(**kw)
            self._api = "v3"
            return self._ocr
        except (TypeError, ValueError):
            # older 2.x API: different constructor keywords
            self._ocr = PaddleOCR(lang=self.lang, use_angle_cls=self.use_textline_orientation)
            self._api = "v2"
            return self._ocr

    def _engine(self):
        if self._ocr is None:
            self._build()
        return self._ocr

    def ocr_image(self, png_bytes: bytes):
        """Run OCR on PNG bytes. Returns (text, mean_confidence), lines in reading order."""
        import numpy as np
        from PIL import Image

        img = np.array(Image.open(io.BytesIO(png_bytes)).convert("RGB"))
        eng = self._engine()
        if self._api != "v3":
            return parse_ocr(eng.ocr(img))
        try:
            return parse_predict(eng.predict(img))
        except AttributeError:                      # a 3.x build without predict(): use ocr()
            return parse_ocr(eng.ocr(img))
        except NotImplementedError:
            # the oneDNN/PIR CPU crash: rebuild without MKL-DNN and retry once
            if self.enable_mkldnn is False:
                raise
            self.enable_mkldnn = False
            self._ocr = None
            return parse_predict(self._engine().predict(img))

    def ocr_page(self, pdf_doc, page_index: int):
        """Render page `page_index` (0-based) of a PdfDoc and OCR it."""
        png = pdf_doc.render_png(page_index, dpi=self.dpi)
        return self.ocr_image(png)
