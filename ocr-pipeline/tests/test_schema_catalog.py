"""The JSON schema round-trips, and catalog reading coerces types."""

import csv

from bctc_ingest.catalog import load_catalog, metadata_for, routing_hint
from bctc_ingest.schema import Chunk, DocChunks


def test_chunk_counts_filled():
    c = Chunk(chunk_id="D::0000", doc_id="D", text="mot hai ba", pages=[1],
              extract_method="text")
    assert c.n_chars == 10 and c.n_tokens_est == 3


def test_docchunks_roundtrip(tmp_path):
    chunks = [Chunk(chunk_id=f"D::{i:04d}", doc_id="D", text=f"chunk {i}", pages=[i + 1],
                    extract_method="text", ticker="VNM", year=2025) for i in range(3)]
    dc = DocChunks(doc_id="D", source_file="D.pdf", extract_method="text", n_pages=3,
                   chunks=chunks, metadata={"ticker": "VNM"})
    path = dc.write(tmp_path / "D.json")
    back = DocChunks.read(path)
    assert back.n_chunks == 3
    assert back.doc_id == "D" and back.chunks[0].ticker == "VNM"
    assert back.to_dict() == dc.to_dict()


def test_catalog_load_and_coerce(tmp_path):
    p = tmp_path / "catalog.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "ticker", "year", "quarter", "audited",
                                           "kind", "text_quality", "period", "scope", "unit",
                                           "exchange"])
        w.writeheader()
        w.writerow(dict(file="VNM_x.pdf", ticker="VNM", year="2025", quarter="0",
                        audited="True", kind="scanned", text_quality="no_diacritics",
                        period="2025", scope="hopnhat", unit="trieu", exchange="HOSE"))
        w.writerow(dict(file="FPT_y.pdf", ticker="FPT", year="2024", quarter="3",
                        audited="False", kind="digital", text_quality="ok",
                        period="2024Q3", scope="hopnhat", unit="dong", exchange="HOSE"))
    cat = load_catalog(p)
    md = metadata_for(cat["VNM_x.pdf"])
    assert md["year"] == 2025 and md["audited"] is True and md["ticker"] == "VNM"
    # routing hints from the catalog
    assert routing_hint(cat["VNM_x.pdf"]) == "ocr"      # scanned
    assert routing_hint(cat["FPT_y.pdf"]) == "text"     # digital + ok
    assert routing_hint({}) is None


def test_load_missing_catalog_is_empty(tmp_path):
    assert load_catalog(tmp_path / "nope.csv") == {}
