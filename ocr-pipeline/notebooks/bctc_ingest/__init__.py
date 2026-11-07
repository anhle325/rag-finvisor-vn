"""bctc_ingest — turn a corpus of Vietnamese financial-statement PDFs into retrieval chunks.

Input : PDFs (structured, scanned, or mixed), optionally with the data-pipeline's catalog.csv.
Output: one JSON file of chunks per document, under <out>/chunks/<TICKER>/<doc_id>.json,
        plus a manifest.csv index and an optional chunks_all.jsonl export.

    python -m bctc_ingest run --corpus path/to/corpus --catalog catalog.csv --out ingest_out

Each page is routed to text extraction (PyMuPDF / pypdf) or OCR (PaddleOCR, CPU). PaddleOCR
is an optional dependency, imported lazily, so the text path runs without it.
"""

__version__ = "0.1.0"
