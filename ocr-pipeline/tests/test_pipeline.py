"""End-to-end on a real (generated) PDF, with no OCR engine — the text path and the
graceful behaviour when OCR is requested but PaddleOCR is not installed.
"""

import json

from bctc_ingest.pipeline import IngestConfig, export_jsonl, ingest_document


def test_text_path_writes_chunks_with_metadata(text_pdf, meta, tmp_path):
    cfg = IngestConfig(chunk_size=500, overlap=80)
    res = ingest_document(text_pdf, tmp_path / "out", cfg, meta=meta, ocr_engine=None,
                          force="text")
    assert res["status"] == "ok"
    assert res["extract_method"] == "text"
    assert res["n_ocr_pages"] == 0
    assert res["n_chunks"] >= 1

    out_json = tmp_path / "out" / res["json_path"]
    assert out_json.exists()
    doc = json.loads(out_json.read_text(encoding="utf-8"))

    # chunks carry the document metadata and a stable, ordered id
    c0 = doc["chunks"][0]
    assert c0["chunk_id"] == "VNM_BCTC_HN_N2025::0000"
    assert c0["ticker"] == "VNM" and c0["year"] == 2025 and c0["period"] == "2025"
    assert c0["extract_method"] == "text"
    assert c0["pages"] and all(isinstance(p, int) for p in c0["pages"])
    # laid out under chunks/<TICKER>/<doc_id>.json
    assert res["json_path"].replace("\\", "/") == "chunks/VNM/VNM_BCTC_HN_N2025.json"


def test_ocr_requested_but_unavailable_is_graceful(text_pdf, meta, tmp_path):
    cfg = IngestConfig()
    res = ingest_document(text_pdf, tmp_path / "out", cfg, meta=meta, ocr_engine=None,
                          force="ocr")
    # every page routed to OCR, no engine -> recorded, not crashed
    assert res["n_ocr_pages"] == res["n_pages"]
    assert res["status"] == "ocr_unavailable"


def test_export_jsonl_flattens_all_chunks(text_pdf, meta, tmp_path):
    cfg = IngestConfig(chunk_size=500, overlap=80)
    out = tmp_path / "out"
    ingest_document(text_pdf, out, cfg, meta=meta, ocr_engine=None, force="text")
    dest = export_jsonl(out)
    lines = dest.read_text(encoding="utf-8").strip().splitlines()
    assert lines
    rec = json.loads(lines[0])
    assert rec["doc_id"] == "VNM_BCTC_HN_N2025" and "text" in rec
