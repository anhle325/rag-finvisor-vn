# ocr-pipeline — PDF → chunked JSON

**Turn a corpus of Vietnamese financial-statement PDFs (structured *or* scanned) into
retrieval chunks saved as JSON**, ready for the embedding/indexing step of `rag-finvisor-vn`.

This is component 2 of the system; it consumes what the [`data-pipeline`](../data-pipeline/)
produces (`corpus/*.pdf` + `catalog.csv`).

```
PDF ──► route ──► extract (text)  ─┐
         │        PyMuPDF / pypdf   │
         │                          ├─► clean ──► chunk ──► chunks/<TICKER>/<doc_id>.json
         └──────► OCR (PaddleOCR) ──┘                        + manifest.csv
                  scanned pages, CPU
```

Each **page** is routed independently: a usable Vietnamese text layer is read directly; a
scanned page is OCR'd with PaddleOCR. A document with both becomes `extract_method = mixed`.

## Install

From this folder:

```bash
cd ocr-pipeline
python -m venv .venv && source .venv/bin/activate     # or: uv venv && source .venv/bin/activate
pip install -e ".[dev]"            # core (PyMuPDF, pypdf) + pytest
pip install -e ".[ocr]"            # add OCR: PaddleOCR + PaddlePaddle (CPU), Pillow, numpy
```

> **OCR is optional.** Without the `[ocr]` extra the text path works fully; a scanned page is
> recorded as `ocr_unavailable` instead of crashing. Install `[ocr]` to process scans — it
> pulls **PaddleOCR 3.x** and the CPU build of PaddlePaddle. The first run downloads the small
> `PP-OCRv5_mobile` detection/recognition models once. On Apple Silicon (M1), install the CPU
> wheel of `paddlepaddle` and run Python natively (arm64), not under Rosetta. On some Windows
> CPU builds PaddlePaddle crashes inside oneDNN (`NotImplementedError: ... onednn_instruction.cc`);
> the engine auto-retries with `enable_mkldnn=False`, or construct `PaddleOCREngine(enable_mkldnn=False)`.

## Run

```bash
# the usual case: a corpus built by the data-pipeline, with its catalog
python -m bctc_ingest run \
    --corpus ../data-pipeline/pack_real/corpus \
    --catalog ../data-pipeline/pack_real/catalog.csv \
    --out ingest_out

python -m bctc_ingest run --corpus ./pdfs --out ingest_out --force-ocr   # OCR everything
python -m bctc_ingest one --pdf report.pdf --out ingest_out              # a single file
python -m bctc_ingest export --out ingest_out                            # -> chunks_all.jsonl
```

Useful options: `--chunk-size 1200 --overlap 150` (chunking), `--dpi 200` (OCR render),
`--ocr-lang vi`, `--force-ocr` / `--force-text`, `--max-docs N` (smoke test), `--force`
(reprocess). The run is **resumable** — a `manifest.csv` records finished documents and a
re-run skips them.

## Output

```
ingest_out/
├── chunks/
│   ├── VNM/VNM_BCTC_HN_N2025.json     # one JSON per document (report)
│   └── FPT/FPT_BCTC_HN_Q3_2025.json
├── manifest.csv                        # index: doc_id, status, method, #chunks, #ocr pages
└── chunks_all.jsonl                    # optional flat export (one chunk per line)
```

Each chunk carries the document's metadata (ticker, year, period, scope, unit, …) and a
stable id `"<doc_id>::<index>"`, so the retrieval layer can filter and the vector store can
upsert without duplicating. Full schema and the "why one file per document / what about a
database" rationale: **[docs/STORAGE.md](docs/STORAGE.md)**.

## Two ways to learn it

- **Notebook:** [`notebooks/ocr_ingestion_lab.ipynb`](notebooks/ocr_ingestion_lab.ipynb) —
  a step-by-step walkthrough (routing → extract → OCR → clean → chunk → JSON) that runs
  offline on a generated sample.
- **Script:** [`examples/run_demo.py`](examples/run_demo.py) — the same flow as a plain
  Python file: `python examples/run_demo.py`.

## Develop & test

```bash
pytest           # 18 tests, fully offline, no OCR engine required
```

## Layout

```
ocr-pipeline/
├── src/bctc_ingest/
│   ├── cli.py          # python -m bctc_ingest run|one|export
│   ├── pipeline.py     # orchestrate one document / a whole corpus
│   ├── extract.py      # PDF text + page rendering (PyMuPDF / pypdf)
│   ├── route.py        # per-page decision: text vs OCR
│   ├── ocr_paddle.py   # PaddleOCR wrapper (optional, lazy import)
│   ├── clean.py        # Unicode/whitespace normalise, strip repeated furniture
│   ├── chunk.py        # paragraph packing with overlap, page-span tracking
│   ├── catalog.py      # read the data-pipeline's catalog.csv
│   └── schema.py       # Chunk / DocChunks + JSON I/O
├── notebooks/ocr_ingestion_lab.ipynb
├── examples/run_demo.py
├── tests/
└── docs/STORAGE.md
```

## License

MIT — see [LICENSE](LICENSE).
