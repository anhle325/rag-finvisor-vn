"""Command line: python -m bctc_ingest <command> [options]

    run      process every PDF in a corpus folder -> chunks/<TICKER>/<doc_id>.json + manifest.csv
    one      process a single PDF
    export   flatten all per-document JSON into one chunks_all.jsonl

Examples

    # the usual case: a corpus built by the data-pipeline component, with its catalog
    python -m bctc_ingest run --corpus ../data-pipeline/pack_real/corpus \\
                              --catalog ../data-pipeline/pack_real/catalog.csv \\
                              --out ingest_out

    python -m bctc_ingest run --corpus ./pdfs --out ingest_out --force-ocr   # all via OCR
    python -m bctc_ingest one --pdf VNM_BCTC_HN_N2025.pdf --out ingest_out
    python -m bctc_ingest export --out ingest_out
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .catalog import load_catalog, metadata_for, routing_hint
from .pipeline import IngestConfig, export_jsonl, ingest_corpus, ingest_document


def _cfg(args) -> IngestConfig:
    force = "ocr" if args.force_ocr else "text" if args.force_text else None
    return IngestConfig(chunk_size=args.chunk_size, overlap=args.overlap, dpi=args.dpi,
                        ocr_lang=args.ocr_lang, force=force,
                        min_chars=args.min_chars, min_diacritic=args.min_diacritic)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bctc_ingest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "one", "export"])
    ap.add_argument("--corpus", help="folder of PDFs (run)")
    ap.add_argument("--pdf", help="a single PDF (one)")
    ap.add_argument("--catalog", help="catalog.csv from the data-pipeline (optional)")
    ap.add_argument("--out", default="ingest_out", help="output folder")
    ap.add_argument("--chunk-size", type=int, default=1200, help="target characters per chunk")
    ap.add_argument("--overlap", type=int, default=150, help="overlap characters between chunks")
    ap.add_argument("--dpi", type=int, default=200, help="render DPI for OCR")
    ap.add_argument("--ocr-lang", default="vi", help="PaddleOCR language (vi, en, ...)")
    ap.add_argument("--force-ocr", action="store_true", help="OCR every page")
    ap.add_argument("--force-text", action="store_true", help="never OCR; use the text layer")
    ap.add_argument("--min-chars", type=int, default=120,
                    help="min characters for a page's text layer to count as usable")
    ap.add_argument("--min-diacritic", type=float, default=0.12,
                    help="min share of Vietnamese-diacritic words for usable text")
    ap.add_argument("--max-docs", type=int, help="process at most N documents (smoke test)")
    ap.add_argument("--force", action="store_true", help="reprocess documents already done")
    ap.add_argument("--ticker", help="one: override the ticker")
    args = ap.parse_args(argv)

    if args.force_ocr and args.force_text:
        sys.exit("--force-ocr and --force-text are mutually exclusive")
    cfg = _cfg(args)

    if args.command == "run":
        if not args.corpus:
            sys.exit("run needs --corpus <folder of PDFs>")
        manifest = ingest_corpus(args.corpus, args.out, cfg, catalog_path=args.catalog,
                                 force_reprocess=args.force, max_docs=args.max_docs)
        from collections import Counter
        st = Counter(r["status"] for r in manifest.values())
        print(f"done: {dict(st)}  ({len(manifest)} documents) -> {args.out}")

    elif args.command == "one":
        if not args.pdf:
            sys.exit("one needs --pdf <file>")
        row = load_catalog(args.catalog).get(Path(args.pdf).name, {}) if args.catalog else {}
        meta = metadata_for(row)
        if args.ticker:
            meta["ticker"] = args.ticker.upper()
        from .pipeline import _maybe_ocr_engine
        res = ingest_document(args.pdf, args.out, cfg, meta=meta,
                              ocr_engine=_maybe_ocr_engine(cfg),
                              force=cfg.force or routing_hint(row))
        print(res)

    elif args.command == "export":
        export_jsonl(args.out)


if __name__ == "__main__":
    main()
