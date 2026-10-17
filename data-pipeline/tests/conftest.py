"""Shared fixtures. Every test runs OFFLINE against the recorded replay cache in
`tests/replay/` — no test ever opens a network socket.
"""

from pathlib import Path

import pytest

REPLAY_DIR = Path(__file__).parent / "replay"


@pytest.fixture
def replay_dir() -> Path:
    return REPLAY_DIR


@pytest.fixture
def client():
    """A ReplayClient that answers from the recorded cache only."""
    from bctc_collector.replay import ReplayClient
    return ReplayClient(REPLAY_DIR)


@pytest.fixture
def discovered(client):
    """Records discovered for VNM + SHS, year 2025, classified — the recorded set."""
    from bctc_collector.sources import CafefSource, VietstockSource
    from bctc_collector.titles import classify

    recs = []
    for Src in (VietstockSource, CafefSource):
        src = Src(client)
        for ticker in ("VNM", "SHS"):
            recs += [classify(r) for r in src.discover(ticker, [2025])]
    return recs
