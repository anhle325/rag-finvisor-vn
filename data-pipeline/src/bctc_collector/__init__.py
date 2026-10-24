"""
bctc_collector — build a corpus of REAL Vietnamese financial statements (BCTC)
from a list of stock tickers.

    python -m bctc_collector run --tickers VNM,FPT,HPG --years 2023-2025 --out pack_real

Stages (each one reads the previous stage's files, so every stage is resumable):

    discover  -> discovery.jsonl       every document any source knows about
    plan      -> plan.csv              one row per document identity, best source + fallbacks
    download  -> raw/ + download_log.csv
    ingest    -> corpus/*.pdf + catalog.csv   (unpack zip/rar, probe, verify)
    reference -> reference_financials.csv     (structured figures, for evaluation)

The output folder has the same shape as the course's synthetic data pack
(corpus/, catalog.csv, reference_financials.csv, companies.csv), so the course
notebooks can point at it with RAG_PACK=pack_real.
"""

__version__ = "0.1.0"
