"""Fetch the bytes for every planned document — resumable, validated, with fallbacks.

For each plan row we try, in order: every candidate (best source first), and for
each candidate its URL then its mirrors. A download counts only if the first
bytes say it is a document (%PDF / PK / Rar!) — portals answer a missing file
with an HTML page or a JSON error and status 200 surprisingly often.
"""

from __future__ import annotations

import csv
import hashlib
import json
import time
from pathlib import Path

MAGIC = ((b"%PDF-", ".pdf"), (b"PK\x03\x04", ".zip"), (b"Rar!\x1a\x07", ".rar"),
         (b"7z\xbc\xaf\x27\x1c", ".7z"))

LOG_FIELDS = ["doc_id", "status", "source", "url", "http", "bytes", "ext", "sha256",
              "attempts", "raw_path", "seconds"]


def sniff(head: bytes):
    for sig, ext in MAGIC:
        if head.startswith(sig):
            return ext
    # some servers prepend a few bytes of junk before %PDF
    if b"%PDF-" in head[:1024]:
        return ".pdf"
    return None


def sha256_file(path, block=1 << 16):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def _referer(url):
    if "vietstock" in url:
        return "https://finance.vietstock.vn/"
    if "cafef" in url or "mediacdn" in url:
        return "https://cafef.vn/"
    return None


def read_log(path):
    if not Path(path).exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        return {r["doc_id"]: r for r in csv.DictReader(fh)}


def download_all(plan_rows, client, out_dir, only_latest=False, max_docs=None, verbose=True):
    out_dir = Path(out_dir)
    raw_dir = out_dir / "raw"
    log_path = out_dir / "download_log.csv"
    log = read_log(log_path)

    rows = [r for r in plan_rows if not only_latest or str(r["is_latest"]) in ("True", "1")]
    if max_docs:
        rows = rows[:max_docs]

    for i, row in enumerate(rows, 1):
        doc_id = row["doc_id"]
        cands = row["candidates"]
        cands = json.loads(cands) if isinstance(cands, str) else cands
        allowed = {u for c in cands for u in [c["url"]] + list(c.get("mirrors") or [])}
        prev = log.get(doc_id)
        # resume only if the file on disk came from one of THIS plan row's URLs —
        # a re-plan can re-assign a doc_id (e.g. version numbers) to another record
        if (prev and prev["status"] == "ok" and prev["url"] in allowed
                and (out_dir / prev["raw_path"]).exists()):
            if verbose:
                print(f"[{i}/{len(rows)}] cached   {doc_id}")
            continue

        t0, attempts, result = time.time(), 0, None
        for c in cands:
            for url in [c["url"]] + list(c.get("mirrors") or []):
                attempts += 1
                tmp = raw_dir / row["ticker"] / f"{doc_id}.download"
                hdr = {"Referer": _referer(url)} if _referer(url) else {}
                try:
                    code, n, head = client.stream_to_file(url, tmp, headers=hdr)
                except Exception as e:                          # noqa: BLE001
                    code, n, head = f"error_{type(e).__name__}", 0, b""
                if code != 200:
                    result = dict(status=f"http_{code}" if isinstance(code, int) else code,
                                  source=c["source"], url=url, http=code)
                    continue
                ext = sniff(head)
                if ext is None:
                    tmp.unlink(missing_ok=True)
                    result = dict(status="not_a_document", source=c["source"], url=url,
                                  http=code)
                    continue
                dest = tmp.with_name(doc_id + ext)
                tmp.replace(dest)
                result = dict(status="ok", source=c["source"], url=url, http=200, bytes=n,
                              ext=ext, sha256=sha256_file(dest),
                              raw_path=str(dest.relative_to(out_dir)))
                break
            if result and result["status"] == "ok":
                break

        result = result or dict(status="no_candidates")
        result.update(doc_id=doc_id, attempts=attempts, seconds=round(time.time() - t0, 2))
        log[doc_id] = {k: result.get(k, "") for k in LOG_FIELDS}
        if verbose:
            tag = "saved   " if result["status"] == "ok" else "FAILED  "
            extra = (f"{result.get('bytes', 0) / 1e6:.1f} MB {result.get('ext')} "
                     f"via {result.get('source')}" if result["status"] == "ok"
                     else result["status"])
            print(f"[{i}/{len(rows)}] {tag} {doc_id}  ({extra}, {attempts} tries)")
        _write_log(log_path, log)
    return log


def _write_log(path, log):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        w.writeheader()
        for r in log.values():
            w.writerow(r)
    tmp.replace(path)
