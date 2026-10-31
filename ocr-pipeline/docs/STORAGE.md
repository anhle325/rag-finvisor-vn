# Storage design: how chunks are stored, and where the database fits

This document answers two questions: **with many reports across many tickers, do we write one
big JSON or many JSON files?** and **does this later become a database?**

## Short answer

- **One JSON file per document (per report), partitioned by ticker.** Not one giant combined
  file, and not one file per chunk.
- **Yes, a database comes later** — but at the *next* step. These JSON files are the durable,
  inspectable input to an embedding step that writes vectors + metadata into a **vector
  database**. The JSON is the staging layer; the DB is the serving layer.

## Why one file per document

The document (one financial report = one PDF) is the natural unit of work, so making it the
unit of storage gives us, almost for free, the properties a batch pipeline needs:

| Property | One file per document | One giant JSON | One file per chunk |
|---|---|---|---|
| Add one report | append a file | **rewrite the whole file** | append thousands of files |
| Re-process one report | rewrite one file | rewrite everything | delete+write many files |
| Resume after a crash | skip finished files | hard | per-file bookkeeping |
| Parallel workers | no contention | **write contention** | many tiny writes |
| Open / inspect by hand | easy (one report) | must load all of it | scattered |
| Memory to read one report | small | **load everything** | reassemble from many |

A giant combined file does not scale: every new report forces a full rewrite, and two workers
can't write at once. One-file-per-chunk swings too far the other way — thousands of tiny files
per corpus, slow to list and move. **One file per document** sits in the middle and matches
how the crawler already thinks (one `doc_id` = one report).

Layout:

```
ingest_out/
├── chunks/
│   ├── VNM/
│   │   ├── VNM_BCTC_HN_N2025.json
│   │   └── VNM_BCTC_HN_Q3_2025.json
│   └── FPT/ ...
├── manifest.csv          # index across all documents: doc_id, status, method, #chunks
└── chunks_all.jsonl      # optional: every chunk on one line, for bulk embedding
```

Partitioning by ticker keeps each folder small and makes "re-ingest everything for VNM" a
one-folder operation. `manifest.csv` is the cheap index you scan to answer "what's done, what
failed, how many chunks" without opening every JSON.

### The JSONL export

`chunks_all.jsonl` (one chunk per line) is a convenience for the embedding step: it streams
the whole corpus without walking the tree, and one malformed line never breaks the rest. It is
**derived** from the per-document JSONs (`bctc_ingest export`), which remain the source of
truth — never hand-edit the JSONL.

## The chunk record

Each chunk is self-describing: it carries the document metadata so retrieval can filter and
cite without a second lookup.

```json
{
  "chunk_id": "VNM_BCTC_HN_N2025::0007",
  "doc_id": "VNM_BCTC_HN_N2025",
  "text": "…",
  "pages": [12, 13],
  "extract_method": "ocr",
  "n_chars": 980,
  "n_tokens_est": 240,
  "ocr_confidence": 0.972,
  "ticker": "VNM", "year": 2025, "quarter": 0, "period": "2025",
  "scope": "hopnhat", "audited": true, "unit": "trieu", "exchange": "HOSE"
}
```

`chunk_id = "<doc_id>::<index>"` is **deterministic**: re-running the pipeline produces the
same ids, so a vector store can *upsert* (replace) instead of accumulating duplicates.

## Where the database fits

The ingestion output is **not** the final store — it is the staging format. The pipeline after
this one does:

```
chunks/*.json ──► embed each chunk's text ──► upsert (vector, metadata) into a vector DB
                   (e.g. BAAI/bge-m3)            (Qdrant / pgvector / FAISS / ...)
```

- The **vector database** holds the embedding + the chunk metadata, and serves similarity
  search with metadata filters (ticker, year, scope) at query time.
- The **structured figures** the crawler produced (`reference_financials.csv`) can go into a
  relational table for exact numeric lookups and for evaluation — complementary to the vector
  search over narrative text.

Keep the JSON chunks even after the DB exists. They are reproducible and model-agnostic: to
try a new embedding model you re-embed from JSON — **no re-OCR, no re-chunking**. Treat the
JSON as the system's durable record and the vector DB as a rebuildable index over it.

## Practical notes

- Ingestion output (`ingest_out/`, `*.jsonl`) is **data, not source** — it is git-ignored and
  rebuilt by command, exactly like the crawler's `pack_*/`.
- Keep `doc_id` identical to the crawler's so the two components join cleanly on it.
- Chunk size is a retrieval-quality knob, not a storage concern: change `--chunk-size` /
  `--overlap` and re-run; the storage layout is unaffected.
