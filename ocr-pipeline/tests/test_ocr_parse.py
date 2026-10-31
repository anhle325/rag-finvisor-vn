"""Parsing of PaddleOCR output — both the 3.x (`predict`) and 2.x (`ocr`) shapes.

These exercise the result-handling without installing PaddleOCR: the engine's heavy model is
never built, only the pure parse functions are called with representative data.
"""

from bctc_ingest.ocr_paddle import parse_ocr, parse_predict


class _V3Result(dict):
    """Stand-in for a PaddleOCR 3.x OCRResult (dict-like with the rec_* keys)."""


def test_parse_predict_v3_orders_by_position_and_averages_conf():
    # two lines given out of reading order; polys put "Top" above "Bottom"
    res = _V3Result(
        rec_texts=["Bottom line", "Top line"],
        rec_scores=[0.90, 0.80],
        rec_polys=[[[0, 200], [50, 200], [50, 220], [0, 220]],     # y=200 -> lower
                   [[0, 10], [50, 10], [50, 30], [0, 30]]],         # y=10  -> upper
    )
    text, conf = parse_predict([res])
    assert text == "Top line\nBottom line"          # reordered top-to-bottom
    assert conf == round((0.90 + 0.80) / 2, 4)


def test_parse_predict_handles_json_wrapper():
    class Wrapped:
        def __init__(self, d):
            self.json = {"res": d}
        def __getitem__(self, k):                     # no dict-like rec_texts access
            raise KeyError(k)
    d = dict(rec_texts=["A", "B"], rec_scores=[1.0, 1.0],
             rec_polys=[[[0, 0], [1, 0], [1, 1], [0, 1]], [[0, 5], [1, 5], [1, 6], [0, 6]]])
    text, conf = parse_predict([Wrapped(d)])
    assert text == "A\nB" and conf == 1.0


def test_parse_predict_empty():
    assert parse_predict([]) == ("", 0.0)
    assert parse_predict(None) == ("", 0.0)


def test_parse_ocr_v2_shape():
    result = [[
        [[[0, 10], [9, 10], [9, 20], [0, 20]], ("Dong tien", 0.95)],
        [[[0, 40], [9, 40], [9, 50], [0, 50]], ("Loi nhuan", 0.85)],
    ]]
    text, conf = parse_ocr(result)
    assert text == "Dong tien\nLoi nhuan"
    assert conf == round((0.95 + 0.85) / 2, 4)


def test_parse_ocr_empty():
    assert parse_ocr([None]) == ("", 0.0)
    assert parse_ocr([]) == ("", 0.0)
