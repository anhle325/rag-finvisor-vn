"""Orchestrate one document, and a whole corpus, into chunked JSON.

Per document:   open PDF -> route each page -> extract text or OCR -> clean -> chunk -> write
                <out>/chunks/<TICKER>/<doc_id>.json
Per corpus:     iterate the PDFs, skip ones already done (resumable), append to manifest.csv

OCR is optional: if a page needs OCR and no PaddleOCR engine is available, the document is
written with whatever text pages it has and its status records `ocr_unavailable`, so a run
never crashes on a missing optional dependency.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from . import clean as _clean
from . import route as _route
from .catalog import load_catalog, metadata_for, routing_hint
from .extract import PdfDoc, have_pymupdf
from .chunk import chunk_pages
from .schema import Chunk, DocChunks

MANIFEST_FIELDS = ["doc_id", "ticker", "source_file", "status", "extract_method",
                   "n_pages", "n_ocr_pages", "n_chunks", "mean_ocr_conf", "json_path"]


@dataclass
class IngestConfig:
    chunk_size: int = 1200
    overlap: int = 150
    dpi: int = 200
    ocr_lang: str = "vi"
    force: str = None            # None | "text" | "ocr"  (overrides routing)
    min_chars: int = 120
    min_diacritic: float = 0.12


def _ticker_of(doc_id: str, meta: dict) -> str:
    if meta.get("ticker"):
        return str(meta["ticker"]).upper()
    return doc_id.split("_", 1)[0].upper()      # fall back to the file-name prefix


def ingest_document(pdf_path, out_dir, cfg: IngestConfig, meta: dict = None,
                    ocr_engine=None, force=None) -> dict:
    """Process one PDF into `<out>/chunks/<TICKER>/<doc_id>.json`. Returns a manifest row."""
    pdf_path = Path(pdf_path)
    doc_id = pdf_path.stem
    meta = meta or {}
    ticker = _ticker_of(doc_id, meta)
    force = force or cfg.force

    with PdfDoc(pdf_path) as doc:
        raw_pages = doc.page_texts()
        methods = _route.route_pages(raw_pages, cfg.min_chars, cfg.min_diacritic, force=force)

        page_text, page_conf, n_ocr = [], [], 0
        ocr_failed = False
        for i, (text, how) in enumerate(zip(raw_pages, methods)):
            if how == "ocr":
                n_ocr += 1
                if ocr_engine is not None:
                    try:
                        text, conf = ocr_engine.ocr_page(doc, i)
                        page_conf.append(conf)
                    except Exception:                           # noqa: BLE001
                        ocr_failed = True
                        text = ""
                else:
                    ocr_failed = True
                    text = ""
            page_text.append(text)
        n_pages = doc.n_pages

    # clean (strip repeated furniture across pages, normalise), keep page numbers
    cleaned = _clean.strip_furniture([_clean.normalize(t) for t in page_text])
    pages = [(i + 1, t) for i, t in enumerate(cleaned) if t.strip()]

    raw_chunks = chunk_pages(pages, cfg.chunk_size, cfg.overlap)
    method = _route.doc_method(methods)
    mean_conf = round(sum(page_conf) / len(page_conf), 4) if page_conf else None

    chunks = []
    for idx, ch in enumerate(raw_chunks):
        # a chunk's method is 'ocr' if any page it spans was OCR'd
        spans_ocr = any(methods[p - 1] == "ocr" for p in ch["pages"] if p - 1 < len(methods))
        chunks.append(Chunk(
            chunk_id=f"{doc_id}::{idx:04d}", doc_id=doc_id, text=ch["text"],
            pages=ch["pages"], extract_method="ocr" if spans_ocr else "text",
            ocr_confidence=mean_conf if spans_ocr else None,
            ticker=ticker, year=meta.get("year"), quarter=meta.get("quarter"),
            period=meta.get("period", ""), scope=meta.get("scope", ""),
            audited=meta.get("audited"), unit=meta.get("unit", ""),
            exchange=meta.get("exchange", "")))

    dc = DocChunks(doc_id=doc_id, source_file=pdf_path.name, extract_method=method,
                   n_pages=n_pages, chunks=chunks, metadata=meta,
                   ocr_lang=cfg.ocr_lang if n_ocr else "")
    json_path = Path(out_dir) / "chunks" / ticker / f"{doc_id}.json"
    dc.write(json_path)

    if n_ocr and ocr_failed and ocr_engine is None:
        status = "ocr_unavailable"
    elif n_ocr and ocr_failed:
        status = "ocr_error"
    elif not chunks:
        status = "empty"
    else:
        status = "ok"

    return dict(doc_id=doc_id, ticker=ticker, source_file=pdf_path.name, status=status,
                extract_method=method, n_pages=n_pages, n_ocr_pages=n_ocr,
                n_chunks=len(chunks), mean_ocr_conf=mean_conf if mean_conf is not None else "",
                json_path=str(json_path.relative_to(out_dir)))


def _read_manifest(path):
    if not Path(path).exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        return {r["doc_id"]: r for r in csv.DictReader(fh)}


def _write_manifest(path, rows):
    tmp = Path(path).with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows.values())
    tmp.replace(path)


def ingest_corpus(corpus_dir, out_dir, cfg: IngestConfig, catalog_path=None,
                  force_reprocess=False, max_docs=None, verbose=True) -> dict:
    """Process every PDF in `corpus_dir`. Resumable via `<out>/manifest.csv`."""
    corpus_dir, out_dir = Path(corpus_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    catalog = load_catalog(catalog_path) if catalog_path else {}
    manifest_path = out_dir / "manifest.csv"
    manifest = _read_manifest(manifest_path)

    # build the OCR engine lazily, and only if something may need it
    ocr_engine = _maybe_ocr_engine(cfg)

    pdfs = sorted(corpus_dir.rglob("*.pdf"))
    if max_docs:
        pdfs = pdfs[:max_docs]

    for i, pdf in enumerate(pdfs, 1):
        doc_id = pdf.stem
        prev = manifest.get(doc_id)
        if prev and prev.get("status") == "ok" and not force_reprocess \
                and (out_dir / prev["json_path"]).exists():
            if verbose:
                print(f"[{i}/{len(pdfs)}] cached   {doc_id}")
            continue
        row = catalog.get(pdf.name, {})
        meta = metadata_for(row)
        row_result = ingest_document(pdf, out_dir, cfg, meta=meta, ocr_engine=ocr_engine,
                                     force=cfg.force or routing_hint(row))
        manifest[doc_id] = row_result
        _write_manifest(manifest_path, manifest)
        if verbose:
            print(f"[{i}/{len(pdfs)}] {row_result['status']:14s} {doc_id}  "
                  f"({row_result['extract_method']}, {row_result['n_chunks']} chunks, "
                  f"{row_result['n_ocr_pages']}/{row_result['n_pages']} OCR pages)")
    return manifest


def _maybe_ocr_engine(cfg: IngestConfig):
    from . import ocr_paddle
    if not ocr_paddle.available():
        return None
    if not have_pymupdf():
        return None            # OCR needs page rendering, which needs PyMuPDF
    return ocr_paddle.PaddleOCREngine(lang=cfg.ocr_lang, dpi=cfg.dpi)


def export_jsonl(out_dir, dest=None) -> Path:
    """Flatten every `<out>/chunks/**/**.json` into one `chunks_all.jsonl` (one chunk/line).

    Handy for bulk embedding: a downstream step reads it line by line without walking the
    tree. The per-document JSONs remain the source of truth."""
    out_dir = Path(out_dir)
    dest = Path(dest) if dest else out_dir / "chunks_all.jsonl"
    import json
    n = 0
    with open(dest, "w", encoding="utf-8") as fh:
        for jp in sorted((out_dir / "chunks").rglob("*.json")):
            dc = DocChunks.read(jp)
            for ch in dc.chunks:
                fh.write(json.dumps(ch.to_dict(), ensure_ascii=False) + "\n")
                n += 1
    print(f"export: {n} chunks -> {dest}")
    return dest
