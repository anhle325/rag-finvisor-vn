"""run_report.md — what the sources have, what we took, what it looks like, in one page.

Written for the spike-then-full workflow: the INVENTORY part describes everything the
sources list (from plan.csv), the SAMPLE part describes what was actually downloaded,
and the ESTIMATE part extrapolates one to the other (size, pages, OCR hours).
"""

from __future__ import annotations

import csv
import statistics
from collections import Counter, defaultdict
from pathlib import Path

OCR_CPU_SECONDS_PER_PAGE = 2.2      # measured: OCRmyPDF 17 + tesseract vie, --skip-text


def _read(path):
    p = Path(path)
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as fh:
        return list(csv.DictReader(line.replace("\0", "") for line in fh))


def _pct(a, b):
    return f"{a}/{b} ({a / b:.0%})" if b else f"{a}/0"


def write_report(out_dir):
    out = Path(out_dir)
    plan, log = _read(out / "plan.csv"), _read(out / "download_log.csv")
    cat, ev = _read(out / "catalog.csv"), _read(out / "ingest_events.csv")
    dropped = _read(out / "dropped.csv")
    ok = [r for r in log if r["status"] == "ok"]
    latest = [p for p in plan if p["is_latest"] in ("True", "1")]
    L = [f"# Pack report — `{out.name}`", ""]

    # ---------------------------------------------------------------- inventory
    L += ["## Inventory — what the sources list", "",
          f"- planned documents: **{len(plan)}** ({len(latest)} latest versions) · "
          f"tickers: {len({p['ticker'] for p in plan})} · "
          f"dropped records: {len(dropped)} {dict(Counter(d['reason'] for d in dropped).most_common(5))}",
          f"- listed by: {dict(Counter(p['sources'] for p in plan))}",
          f"- by period type: {dict(Counter(p['period_type'] for p in plan))} · "
          f"by scope: {dict(Counter(p['scope'] for p in plan))}",
          f"- identities with >1 version: "
          f"{len({(p['ticker'], p['period'], p['scope'], p['audit']) for p in plan if p['n_versions'] != '1'})}",
          ""]
    years = sorted({p["year"] for p in plan})
    by_ty = Counter((p["ticker"], p["year"]) for p in latest)
    got_ty = Counter()
    plan_by_id = {p["doc_id"]: p for p in plan}
    for r in ok:
        p = plan_by_id.get(r["doc_id"])
        if p:
            got_ty[(p["ticker"], p["year"])] += 1
    tickers = sorted({p["ticker"] for p in plan})
    if tickers and years:
        L += ["### Documents per ticker × year (downloaded / listed, latest versions)", "",
              "| ticker | " + " | ".join(years) + " | total |",
              "|---|" + "---|" * (len(years) + 1)]
        for t in tickers:
            cells = [f"{got_ty[(t, y)]}/{by_ty[(t, y)]}" if by_ty[(t, y)] else "·" for y in years]
            L.append(f"| {t} | " + " | ".join(cells) +
                     f" | {sum(got_ty[(t, y)] for y in years)}/{sum(by_ty[(t, y)] for y in years)} |")
        L.append("")

    # ------------------------------------------------------------------ sample
    L += ["## Downloaded", "",
          f"- downloads: {dict(Counter(r['status'] for r in log))} · "
          f"formats: {dict(Counter(r['ext'] for r in ok))} · via: {dict(Counter(r['source'] for r in ok))}",
          f"- fallbacks needed (attempts > 1): {sum(int(r['attempts'] or 1) > 1 for r in ok)}",
          f"- corpus PDFs: **{len(cat)}**, pages: **{sum(int(r['pages'] or 0) for r in cat)}**",
          f"- kind: {dict(Counter(r['kind'] for r in cat))}",
          f"- text_quality: {dict(Counter(r['text_quality'] for r in cat))}",
          f"- unit found: {dict(Counter(r['unit'] or 'NOT FOUND' for r in cat))}",
          f"- cover check: {dict(Counter(r['meta_check'].split(':')[0] for r in cat))}",
          f"- signed: {_pct(sum(r['signed'] == 'True' for r in cat), len(cat))} · "
          f"cover letter first: {sum(r.get('cover_letter') == 'True' for r in cat)}",
          f"- producers (top 6): "
          f"{dict(Counter((r.get('producer') or '?').split(' / ')[0][:28] for r in cat).most_common(6))}",
          f"- ingest events: {dict(Counter(e['event'].split(':')[0] for e in ev))}", ""]
    by_year = defaultdict(list)
    for r in cat:
        by_year[r["year"]].append(r)
    if by_year:
        L += ["### Scan share by year (does it get better or worse?)", "",
              "| year | files | scanned | mixed | digital | median pages |", "|---|---|---|---|---|---|"]
        for y in sorted(by_year):
            rs = by_year[y]
            k = Counter(r["kind"] for r in rs)
            L.append(f"| {y} | {len(rs)} | {k['scanned']} | {k['mixed']} | {k['digital']} | "
                     f"{statistics.median(int(r['pages'] or 0) for r in rs):.0f} |")
        L.append("")

    # ---------------------------------------------------------------- estimate
    if ok and latest and len(ok) < len(plan):
        mb = [int(r["bytes"] or 0) / 1e6 for r in ok]
        first = [r for r in cat if str(r.get("part", "1")) == "1"]
        # stratify: annual reports are much longer than quarterly ones, and a spike
        # sample does not have the inventory's mix of period types
        pages_by = defaultdict(list)
        for r in first:
            pages_by[r["period_type"]].append(int(r["pages"] or 0))
        allp = [p for v in pages_by.values() for p in v]
        inv = Counter(p["period_type"] for p in latest)
        est_pages = sum(n * statistics.mean(pages_by.get(pt) or allp) for pt, n in inv.items())
        need = sum(r["kind"] != "digital" or r["text_quality"] != "ok" for r in cat) / len(cat)
        ocr = _read(out / "ocr_log.csv")
        spp = [float(r["sec_per_page"]) for r in ocr if r.get("status") == "ok" and r.get("sec_per_page")]
        L += ["## Estimate for the full inventory (latest versions only)", "",
              f"- documents: {len(latest)} {dict(inv)}",
              f"- size: ≈ **{len(latest) * statistics.mean(mb) / 1000:.1f} GB** "
              f"(mean {statistics.mean(mb):.1f} MB, median {statistics.median(mb):.1f} MB per file)",
              "- pages per document in the sample: " + ", ".join(
                  f"{pt} {statistics.mean(v):.0f} (n={len(v)})" for pt, v in sorted(pages_by.items())),
              f"- pages: ≈ **{est_pages:,.0f}** (stratified by period type)",
              f"- needing OCR in the sample: {need:.0%} → ≈ {est_pages * need:,.0f} pages → "
              f"≈ {est_pages * need * OCR_CPU_SECONDS_PER_PAGE / 3600:,.0f} CPU-hours"
              + (f"; at the measured {statistics.median(spp):.2f} s/page (wall) on this machine "
                 f"≈ **{est_pages * need * statistics.median(spp) / 3600:,.0f} hours**" if spp else ""),
              ""]

    bad = [r for r in log if r["status"] != "ok"]
    if bad:
        L += ["## Failed downloads", "", "| doc_id | status | last url tried |", "|---|---|---|"]
        L += [f"| {r['doc_id']} | {r['status']} | {r['url'][:90]} |" for r in bad[:60]]
        L.append("")
    if ev:
        L += ["## Ingest events", "", "| doc_id | event | detail |", "|---|---|---|"]
        L += [f"| {e['doc_id']} | {e['event']} | {e['detail'][:80]} |" for e in ev[:80]]
    p = out / "run_report.md"
    p.write_text("\n".join(x for x in L if x is not None) + "\n", encoding="utf-8")
    return p
