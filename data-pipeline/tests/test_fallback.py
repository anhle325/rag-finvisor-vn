"""The download stage must survive two things real portals do constantly:

  * a CDN node that is dead (404) for a file that is alive on another node, and
  * a server that answers a missing file with an HTML page and status 200.

`FaultyClient` injects both faults on top of the recorded cache, so the lesson is
reproducible offline. This mirrors the simulation cell in the N01c notebook.
"""

import json
from urllib.parse import urlparse

from bctc_collector.download import download_all
from bctc_collector.plan import build_plan, select
from bctc_collector.replay import FaultyClient


def _recorded_cafef_row(plan, recorded):
    """A plan row whose CafeF candidate is a RECORDED file and has CDN mirrors — so a
    mirror can serve the same bytes when the primary host is made to fail."""
    for row in plan:
        for c in json.loads(row["candidates"]):
            if c["source"] == "cafef" and c.get("mirrors") and c["url"] in recorded:
                return row, c
    return None, None


def test_falls_back_to_mirror_when_primary_host_is_dead(discovered, replay_dir, tmp_path):
    kept, _ = select(discovered, years=[2025])
    plan = build_plan(kept)
    recorded = FaultyClient(replay_dir).recorded_doc_urls()
    row, cafef = _recorded_cafef_row(plan, recorded)
    assert row is not None, "fixtures should contain a recorded CafeF file with mirrors"

    flaky = FaultyClient(
        replay_dir,
        dead_hosts={urlparse(cafef["url"]).netloc},      # primary CDN node is dead
        alias={m: cafef["url"] for m in cafef["mirrors"]},  # a mirror serves the same bytes
    )
    log = download_all([row], flaky, tmp_path, verbose=False)

    rec = log[row["doc_id"]]
    assert rec["status"] == "ok"                         # the fallback saved it
    assert urlparse(rec["url"]).netloc != urlparse(cafef["url"]).netloc  # via a mirror
    assert rec["attempts"] >= 2                           # it had to try more than once


def test_html_page_with_status_200_is_rejected(discovered, replay_dir, tmp_path):
    kept, _ = select(discovered, years=[2025])
    plan = build_plan(kept)
    recorded = FaultyClient(replay_dir).recorded_doc_urls()
    row, cafef = _recorded_cafef_row(plan, recorded)

    # every host for this file answers HTML/200 -> nothing is a real document
    hosts = {urlparse(u).netloc for u in [cafef["url"], *cafef["mirrors"]]}
    only_cafef = dict(row, candidates=json.dumps([cafef]))
    flaky = FaultyClient(replay_dir, html_hosts=hosts)

    log = download_all([only_cafef], flaky, tmp_path, verbose=False)
    assert log[row["doc_id"]]["status"] != "ok"          # magic-byte check refused the HTML
