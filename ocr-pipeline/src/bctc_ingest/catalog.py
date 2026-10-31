"""Read the crawler's `catalog.csv` to get each PDF's metadata and a routing hint.

The data-pipeline component writes one `catalog.csv` row per corpus file, with columns like
`file, ticker, year, quarter, period, scope, audited, unit, exchange, kind, text_quality`.
The ingestion step reuses that: the metadata is denormalised onto every chunk, and `kind` /
`text_quality` give a first-pass answer to "does this PDF need OCR?" (still re-checked per
page during routing). Running without a catalog is fine — metadata is then inferred from the
file name and every page is routed by its own content.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .schema import META_KEYS


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_bool(v):
    return str(v).strip().lower() in ("true", "1", "yes")


def load_catalog(path) -> dict:
    """Return {file_name: row_dict}. Empty dict if the file does not exist."""
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(line.replace("\0", "") for line in fh))
    out = {}
    for r in rows:
        key = r.get("file") or r.get("doc_id")
        if key:
            out[key] = r
    return out


def metadata_for(row: dict) -> dict:
    """Pick the metadata keys carried onto chunks, coercing types."""
    row = row or {}
    md = {k: row.get(k, "") for k in META_KEYS}
    md["year"] = _to_int(row.get("year"))
    md["quarter"] = _to_int(row.get("quarter"))
    md["audited"] = _to_bool(row.get("audited")) if row.get("audited") != "" else None
    return md


def routing_hint(row: dict):
    """A document-level hint from the catalog: 'text', 'ocr', or None (decide per page)."""
    if not row:
        return None
    kind = (row.get("kind") or "").lower()
    quality = (row.get("text_quality") or "").lower()
    if kind == "digital" and quality in ("ok", ""):
        return "text"
    if kind == "scanned":
        return "ocr"
    return None            # 'mixed' or unknown -> let per-page routing decide
