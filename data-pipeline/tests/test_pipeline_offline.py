"""The pipeline end-to-end, offline: discover -> plan -> download -> ingest.

Everything is served from the recorded replay cache, so this is deterministic and
needs no network. The two PDFs that ship in the fixtures (VNM annual 2025, SHS Q3
2025) are the only files that actually download; that is enough to exercise every
stage.
"""

import json

from bctc_collector.download import download_all
from bctc_collector.ingest import ingest
from bctc_collector.plan import build_plan, select


def test_discover_finds_records(discovered):
    assert len(discovered) > 20
    assert {r.source for r in discovered} == {"vietstock", "cafef"}
    assert all(r.ticker in ("VNM", "SHS") for r in discovered)


def test_plan_groups_by_identity(discovered):
    kept, dropped = select(discovered, years=[2025])
    plan = build_plan(kept)
    assert plan, "plan should not be empty"
    # one row per (identity, version): doc_id is unique
    ids = [p["doc_id"] for p in plan]
    assert len(ids) == len(set(ids))
    # candidates column is valid JSON carrying at least one source URL
    for p in plan:
        cands = json.loads(p["candidates"])
        assert cands and all("url" in c for c in cands)


def test_download_then_ingest_builds_corpus(client, discovered, tmp_path):
    kept, _ = select(discovered, years=[2025])
    plan = build_plan(kept)

    log = download_all(plan, client, tmp_path, verbose=False)
    ok = {k: v for k, v in log.items() if v["status"] == "ok"}
    # exactly the two recorded files download; the rest 404 offline (expected)
    assert set(ok) == {"SHS_BCTC_DL_Q3_2025", "VNM_BCTC_HN_N2025"}

    rows, _events = ingest(plan, log, tmp_path, verbose=False)
    assert len(rows) == 2
    corpus = sorted(p.name for p in (tmp_path / "corpus").glob("*.pdf"))
    assert corpus == ["SHS_BCTC_DL_Q3_2025.pdf", "VNM_BCTC_HN_N2025.pdf"]

    # the catalog carries the columns the course notebooks read
    assert {"file", "ticker", "year", "period", "scope", "pages", "unit"} <= set(rows[0])
    assert (tmp_path / "catalog.csv").exists()
    assert (tmp_path / "companies.csv").exists()


def test_download_is_resumable(client, discovered, tmp_path):
    kept, _ = select(discovered, years=[2025])
    plan = build_plan(kept)

    log1 = download_all(plan, client, tmp_path, verbose=False)
    ok1 = {k: v["sha256"] for k, v in log1.items() if v["status"] == "ok"}

    # run again on the same output folder: already-downloaded files are reused as-is
    log2 = download_all(plan, client, tmp_path, verbose=False)
    ok2 = {k: v["sha256"] for k, v in log2.items() if v["status"] == "ok"}

    assert ok1 == ok2 and ok1  # same files, same bytes — the run is idempotent
