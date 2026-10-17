"""A polite HTTP client: one session, per-host pacing, retries, an on-disk cache.

Two rules this module exists to enforce:

1. **Never hammer a disclosure portal.** Every request to the same host waits at
   least `delay` seconds after the previous one. 40 students x 30 tickers x 3
   years with no delay is a denial-of-service attack; with a 1.5 s delay it is
   the traffic of one person reading the site.
2. **Never ask twice.** API answers (small JSON) are cached on disk, keyed by
   URL + body. Re-running a stage re-reads the cache, not the website.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
              "bctc-collector/0.1 (course corpus builder; educational use)")


class PoliteClient:
    def __init__(self, cache_dir=None, delay=1.5, timeout=60, retries=3,
                 user_agent=DEFAULT_UA, cache_ttl_hours=24 * 7, session=None):
        self.delay = float(delay)
        self.timeout = timeout
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_ttl = cache_ttl_hours * 3600
        self._last = {}
        self._lock = threading.Lock()
        self.stats = {"requests": 0, "cache_hits": 0, "errors": 0}
        if session is None:
            session = requests.Session()
            retry = Retry(total=retries, backoff_factor=1.5,
                          status_forcelist=(429, 500, 502, 503, 504),
                          allowed_methods=("GET", "POST", "HEAD"))
            adapter = HTTPAdapter(max_retries=retry)
            session.mount("http://", adapter)
            session.mount("https://", adapter)
        session.headers.setdefault("User-Agent", user_agent)
        session.headers["User-Agent"] = user_agent
        session.headers.setdefault("Accept-Language", "vi-VN,vi;q=0.9,en;q=0.6")
        self.session = session

    # ------------------------------------------------------------------ pacing
    def _wait(self, url):
        host = urlparse(url).netloc
        with self._lock:
            last = self._last.get(host, 0.0)
            now = time.monotonic()
            gap = self.delay - (now - last)
            if gap > 0:
                time.sleep(gap)
            self._last[host] = time.monotonic()

    # ------------------------------------------------------------------- cache
    def _cache_path(self, method, url, data):
        if not self.cache_dir:
            return None
        key = json.dumps([method, url, sorted((data or {}).items())], ensure_ascii=False)
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        host = urlparse(url).netloc.replace(":", "_")
        return self.cache_dir / host / f"{h}.json"

    def _cache_get(self, p):
        if p is None or not p.exists():
            return None
        if self.cache_ttl and time.time() - p.stat().st_mtime > self.cache_ttl:
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))["body"]
        except Exception:                                       # noqa: BLE001
            return None

    def _cache_put(self, p, url, body):
        if p is None:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"url": url, "fetched": time.time(), "body": body},
                                  ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    # -------------------------------------------------------------- requests
    def get_text(self, url, headers=None, use_cache=True, cache_key_extra=None):
        p = self._cache_path("GET", url, cache_key_extra) if use_cache else None
        hit = self._cache_get(p)
        if hit is not None:
            self.stats["cache_hits"] += 1
            return hit
        self._wait(url)
        self.stats["requests"] += 1
        r = self.session.get(url, headers=headers or {}, timeout=self.timeout)
        r.raise_for_status()
        r.encoding = r.encoding or "utf-8"
        if use_cache:
            self._cache_put(p, url, r.text)
        return r.text

    def get_json(self, url, headers=None, use_cache=True):
        return json.loads(self.get_text(url, headers=headers, use_cache=use_cache))

    def post_json(self, url, data, headers=None, use_cache=True, on_miss=None):
        """POST form data. `on_miss()` may return extra fields (e.g. a CSRF token)
        that are sent only when the answer is not cached — and never become part
        of the cache key."""
        p = self._cache_path("POST", url, data) if use_cache else None
        hit = self._cache_get(p)
        if hit is not None:
            self.stats["cache_hits"] += 1
            return json.loads(hit)
        if on_miss:
            data = {**data, **on_miss()}
        self._wait(url)
        self.stats["requests"] += 1
        r = self.session.post(url, data=data, headers=headers or {}, timeout=self.timeout)
        r.raise_for_status()
        if use_cache:
            self._cache_put(p, url, r.text)
        return json.loads(r.text)

    def stream_to_file(self, url, dest: Path, headers=None, max_bytes=300 * 2**20):
        """Download `url` into `dest` atomically. Returns (status_code, n_bytes, head16)."""
        self._wait(url)
        self.stats["requests"] += 1
        tmp = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.session.get(url, headers=headers or {}, timeout=self.timeout,
                              stream=True) as r:
            if r.status_code != 200:
                return r.status_code, 0, b""
            n, head = 0, b""
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    if not chunk:
                        continue
                    if not head:
                        head = chunk[:16]
                    n += len(chunk)
                    if n > max_bytes:
                        fh.close()
                        tmp.unlink(missing_ok=True)
                        return -1, n, head
                    fh.write(chunk)
        os.replace(tmp, dest)
        return 200, n, head
