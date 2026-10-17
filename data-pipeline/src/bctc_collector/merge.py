"""Combine packs built by several student groups into one course pack.

    python -m bctc_collector merge --inputs pack_g01,pack_g02,pack_g03 --out pack_all

Rules:
* corpus files are copied once; two groups that downloaded the same bytes (same
  SHA-1) keep one file — the event is logged, not silently dropped;
* the same file NAME with DIFFERENT bytes is a conflict: the second copy is renamed
  `<name>__<group>.pdf` and flagged, because it means two groups disagree about
  what a document is;
* catalog / plan / download_log / reference rows are concatenated with a `group`
  column; reference rows are de-duplicated on (ticker, year, quarter).
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path


def _read(p):
    p = Path(p)
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as fh:
        return list(csv.DictReader(line.replace("\0", "") for line in fh))


def _write(p, rows):
    if not rows:
        return
    fields = []
    for r in rows:
        fields += [k for k in r if k not in fields]
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def merge_packs(inputs, out_dir, verbose=True):
    out = Path(out_dir)
    (out / "corpus").mkdir(parents=True, exist_ok=True)
    (out / "corpus_ocr").mkdir(exist_ok=True)
    cat, plan, log, ref, events, comp = [], [], [], {}, [], {}
    by_sha, by_name = {}, {}
    for src in inputs:
        src = Path(src)
        group = src.name
        for r in _read(src / "catalog.csv"):
            name, sha = r["file"], r["sha1"]
            if sha in by_sha:
                events.append(dict(group=group, event="duplicate_bytes",
                                   detail=f"{name} == {by_sha[sha]}"))
                continue
            if name in by_name:
                new = name[:-4] + f"__{group}.pdf"
                events.append(dict(group=group, event="name_conflict",
                                   detail=f"{name} differs from {by_name[name]}'s -> {new}"))
                name = new
            shutil.copy2(src / "corpus" / r["file"], out / "corpus" / name)
            if r.get("ocr_file") and (src / r["ocr_file"]).exists():
                shutil.copy2(src / r["ocr_file"], out / "corpus_ocr" / name)
                r["ocr_file"] = f"corpus_ocr/{name}"
            by_sha[sha], by_name[name] = name, group
            cat.append(dict(r, file=name, group=group))
        plan += [dict(r, group=group) for r in _read(src / "plan.csv")]
        log += [dict(r, group=group) for r in _read(src / "download_log.csv")]
        for r in _read(src / "companies.csv"):
            comp.setdefault(r["ticker"], r)
        for r in _read(src / "reference_financials.csv"):
            ref.setdefault((r["ticker"], r["year"], r["quarter"]), r)
        if verbose:
            print(f"  {group}: catalog so far {len(cat)} files")
    _write(out / "catalog.csv", cat)
    _write(out / "plan.csv", plan)
    _write(out / "download_log.csv", log)
    _write(out / "reference_financials.csv", list(ref.values()))
    _write(out / "companies.csv", list(comp.values()))
    _write(out / "merge_events.csv", events)
    print(f"merge: {len(cat)} files from {len(inputs)} packs, {len(events)} events -> {out}")
    return cat, events
