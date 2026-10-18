"""replay.py — run bctc_collector with NO network, on answers recorded from the real portals.

`bctc_collector` caches every API answer (small JSON) under <pack>/_http_cache/, keyed
by URL + form data. A real run on VNM + SHS, year 2025 (27/09/2026) left such a cache
behind; `replay/_http_cache/` is a copy of it. `ReplayClient` answers from that copy and
never opens a socket:

    discover / reference  -> the recorded JSON, byte for byte
    download              -> the recorded file for the URLs listed in replay/files.csv,
                             HTTP 404 for anything else

So the numbers an offline run prints are the numbers the live portals returned that
day. What replay cannot show: anything that changed on the portals since, and files
that were not recorded (only two small PDFs ship, to keep the folder light).
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from bctc_collector.http import PoliteClient


class OfflineMiss(ConnectionError):
    """The request was never recorded — in live mode it would go to the portal."""


class ReplayClient(PoliteClient):
    def __init__(self, replay_dir, **kw):
        replay_dir = Path(replay_dir)
        # cache_ttl_hours=0 disables expiry: recorded answers never go stale
        super().__init__(cache_dir=replay_dir / "_http_cache", delay=0, cache_ttl_hours=0, **kw)
        self.files = {}
        manifest = replay_dir / "files.csv"
        if manifest.exists():
            with open(manifest, encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    self.files[r["url"]] = replay_dir / "files" / r["file"]
        self.stats["offline_misses"] = 0

    def _wait(self, url):                       # nothing to be polite to
        pass

    def get_text(self, url, headers=None, use_cache=True, cache_key_extra=None):
        hit = self._cache_get(self._cache_path("GET", url, cache_key_extra))
        if hit is None:
            self.stats["offline_misses"] += 1
            raise OfflineMiss(f"offline: GET {url[:100]} was not recorded")
        self.stats["cache_hits"] += 1
        return hit

    def post_json(self, url, data, headers=None, use_cache=True, on_miss=None):
        hit = self._cache_get(self._cache_path("POST", url, data))
        if hit is None:
            self.stats["offline_misses"] += 1
            raise OfflineMiss(f"offline: POST {url} {data} was not recorded")
        self.stats["cache_hits"] += 1
        return json.loads(hit)

    def stream_to_file(self, url, dest, headers=None, max_bytes=300 * 2**20):
        self.stats["requests"] += 1
        src = self.files.get(url)
        if src is None or not src.exists():
            return 404, 0, b""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        with open(dest, "rb") as fh:
            head = fh.read(16)
        return 200, dest.stat().st_size, head

    def recorded_doc_urls(self):
        return set(self.files)


class FaultyClient(ReplayClient):
    """SIMULATION for the lesson on fallbacks — faults are injected, not observed.

    dead_hosts : host names that answer 404 (a dead CDN node)
    html_hosts : host names that answer 200 with an HTML error page instead of a file
    """

    def __init__(self, replay_dir, dead_hosts=(), html_hosts=(), alias=None, **kw):
        super().__init__(replay_dir, **kw)
        self.dead_hosts, self.html_hosts = set(dead_hosts), set(html_hosts)
        self.alias = alias or {}                # mirror url -> recorded url (same file)

    def stream_to_file(self, url, dest, headers=None, max_bytes=300 * 2**20):
        from urllib.parse import urlparse
        host = urlparse(url).netloc
        if host in self.dead_hosts:
            self.stats["requests"] += 1
            return 404, 0, b""
        if host in self.html_hosts:
            self.stats["requests"] += 1
            body = b"<!DOCTYPE html><html><body>File not found</body></html>"
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            return 200, len(body), body[:16]
        return super().stream_to_file(self.alias.get(url, url), dest, headers, max_bytes)
