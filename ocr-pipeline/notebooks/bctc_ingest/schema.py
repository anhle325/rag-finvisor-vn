"""The on-disk format: one JSON file per source document, holding its chunks.

Design choices (see docs/STORAGE.md for the full rationale):

* **One JSON per document**, not one giant file and not one file per chunk. The document
  (one financial report = one PDF) is the natural unit: it is what the crawler produces, it
  lets the pipeline skip work it already did, and it keeps each file small enough to open.
* **Every chunk carries the document's metadata** (ticker, year, period, scope, ...). The
  retrieval layer needs those fields to filter ("VNM, 2025, consolidated") without loading a
  separate catalog, so they are denormalised onto each chunk.
* **Stable ids.** `chunk_id = "<doc_id>::<index>"` is deterministic, so re-running the
  pipeline produces the same ids and a downstream vector store can upsert instead of
  duplicating.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = "1.0"

# The document-metadata keys copied from the crawler's catalog.csv onto every chunk.
META_KEYS = ("ticker", "year", "quarter", "period", "scope", "audited", "unit", "exchange")


@dataclass
class Chunk:
    """One retrievable piece of text plus the metadata needed to filter and cite it."""

    chunk_id: str
    doc_id: str
    text: str
    pages: list                      # 1-based page numbers this chunk spans, e.g. [3, 4]
    extract_method: str              # "text" | "ocr"
    n_chars: int = 0
    n_tokens_est: int = 0            # rough token estimate (word count), for budgeting
    ocr_confidence: Optional[float] = None   # mean OCR confidence, when extract_method == ocr
    # document metadata (denormalised for the retrieval layer)
    ticker: str = ""
    year: Optional[int] = None
    quarter: Optional[int] = None
    period: str = ""
    scope: str = ""
    audited: Optional[bool] = None
    unit: str = ""
    exchange: str = ""

    def __post_init__(self):
        if not self.n_chars:
            self.n_chars = len(self.text)
        if not self.n_tokens_est:
            self.n_tokens_est = len(self.text.split())

    def to_dict(self):
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d):
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass
class DocChunks:
    """Everything written to one `<doc_id>.json`: the chunks plus provenance."""

    doc_id: str
    source_file: str                 # the PDF file name this came from
    extract_method: str              # "text" | "ocr" | "mixed"
    n_pages: int
    chunks: list = field(default_factory=list)      # list[Chunk]
    metadata: dict = field(default_factory=dict)    # the full catalog row, kept for reference
    ocr_lang: str = ""
    schema_version: str = SCHEMA_VERSION

    @property
    def n_chunks(self):
        return len(self.chunks)

    def to_dict(self):
        return dict(
            doc_id=self.doc_id, source_file=self.source_file,
            extract_method=self.extract_method, n_pages=self.n_pages,
            n_chunks=self.n_chunks, ocr_lang=self.ocr_lang,
            schema_version=self.schema_version, metadata=self.metadata,
            chunks=[c.to_dict() for c in self.chunks])

    @classmethod
    def from_dict(cls, d):
        obj = cls(doc_id=d["doc_id"], source_file=d.get("source_file", ""),
                  extract_method=d.get("extract_method", ""), n_pages=d.get("n_pages", 0),
                  metadata=d.get("metadata", {}), ocr_lang=d.get("ocr_lang", ""),
                  schema_version=d.get("schema_version", SCHEMA_VERSION))
        obj.chunks = [Chunk.from_dict(c) for c in d.get("chunks", [])]
        return obj

    def write(self, path) -> Path:
        """Write atomically to `path` (`.tmp` then rename), creating parent folders."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)
        return path

    @classmethod
    def read(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
