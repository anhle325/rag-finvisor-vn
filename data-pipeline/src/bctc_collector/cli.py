"""Command line: python -m bctc_collector <stage> [options]

    run        discover + plan + download + ingest (+ reference)   <- usual entry point
    discover   ask every source what exists          -> discovery.jsonl
    plan       choose what to download               -> plan.csv, dropped.csv
    download   fetch the bytes                        -> raw/, download_log.csv
    ingest     unpack, probe, verify, catalog         -> corpus/, catalog.csv, companies.csv
    reference  structured figures for evaluation      -> reference_financials.csv
    report     summarise a pack folder                -> run_report.md
    ocr        OPTIONAL: Vietnamese OCR for scans      -> corpus_ocr/, ocr_log.csv, ocr_report.md
               (needs ocrmypdf + tesseract 'vie'; not part of `run`, it takes hours)
    merge      combine packs built by several groups   -> one pack (dedup by SHA-1)
    bench      OCR benchmark pages from existing packs  -> ocr_bench/ + ocr_bench.zip

Examples

    python -m bctc_collector run --tickers VNM,FPT,HPG --years 2023-2025 --out pack_real
    python -m bctc_collector run --tickers-file vn30.txt --years 2024 --periods Q,FY \\
                                 --scopes hopnhat --only-latest --out pack_vn30
    python -m bctc_collector discover --tickers SHS --years 2025 --out tmp   # look first

Spike first, then full (the course workflow)

    # S1: inventory of everything + 3 varied documents per ticker
    python -m bctc_collector run --tickers-file tickers_top50.txt --years 2020-2026 \
                                 --sample-per-ticker 3 --out pack_top50
    # O1: OCR 6 varied documents, first 15 pages only
    python -m bctc_collector ocr --out pack_top50 --sample-docs 6 --ocr-pages 1-15
    # student group 3 of 10: its 5 tickers, everything
    python -m bctc_collector run --tickers-file tickers_top50.txt --shard 3/10 \
                                 --years 2020-2026 --out pack_g03
    python -m bctc_collector merge --inputs pack_g01,pack_g02,... --out pack_all
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

from .download import download_all, read_log
from .http import PoliteClient
from .ingest import ingest
from .models import DocRecord
from .plan import build_plan, parse_periods_by_year, select
from .reference import build_reference
from .sources import CafefSource, IRSiteSource, VietstockSource
from .titles import classify


# ------------------------------------------------------------------ helpers
def parse_years(s):
    out = set()
    for part in str(s).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return sorted(out)


def read_tickers(args):
    ts = []
    if args.tickers:
        ts += [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if args.tickers_file:
        p = Path(args.tickers_file)
        if p.suffix.lower() == ".csv":
            with open(p, encoding="utf-8-sig") as fh:
                rd = csv.DictReader(fh)
                col = next((c for c in rd.fieldnames or [] if c.lower() in
                            ("ticker", "symbol", "ma", "mã ck", "ma ck", "code")),
                           (rd.fieldnames or [None])[0])
                ts += [r[col].strip().upper() for r in rd if r.get(col)]
        else:
            ts += [ln.strip().upper() for ln in p.read_text(encoding="utf-8").splitlines()
                   if ln.strip() and not ln.startswith("#")]
    seen, out = set(), []
    for t in ts:
        if t not in seen:
            seen.add(t)
            out.append(t)
    if not out:
        sys.exit("no tickers: use --tickers VNM,FPT or --tickers-file list.txt")
    shard = getattr(args, "shard", None)
    if shard:
        k, n = (int(x) for x in shard.split("/"))
        if not 1 <= k <= n:
            sys.exit("--shard must look like 3/10 (part 3 of 10)")
        out = out[k - 1::n]            # round-robin: every group gets banks AND non-banks
    return out


def read_ir_map(path):
    if not path:
        return {}
    with open(path, encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh)
        tcol = next(c for c in rd.fieldnames if c.lower() in ("ticker", "mã ck", "symbol"))
        ucol = next(c for c in rd.fieldnames if "ir" in c.lower() or "quan hệ" in c.lower())
        return {r[tcol].strip().upper(): r[ucol].strip() for r in rd if r.get(ucol)}


def make_client(args):
    return PoliteClient(cache_dir=Path(args.out) / "_http_cache", delay=args.delay,
                        timeout=args.timeout)


def _read_csv(path):
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(line.replace("\0", "") for line in fh))


def _write_csv(path, rows, fields=None):
    fields = fields or (list(rows[0].keys()) if rows else ["empty"])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------- stages
def stage_discover(args, client):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    years = parse_years(args.years)
    sources = []
    for name in args.sources.split(","):
        name = name.strip()
        if name == "vietstock":
            sources.append(VietstockSource(client))
        elif name == "cafef":
            sources.append(CafefSource(client))
        elif name == "ir":
            sources.append(IRSiteSource(client, read_ir_map(args.ir_map)))
    recs, failed = [], []

    def ask(t, s):
        try:
            got = [classify(r) for r in s.discover(t, years)]
        except Exception as e:                                  # noqa: BLE001
            print(f"  {t:5s} {s.name:9s} FAILED  {type(e).__name__}: {str(e)[:120]}")
            return None, f"{type(e).__name__}: {str(e)[:200]}"
        print(f"  {t:5s} {s.name:9s} {len(got):4d} records")
        return got, ""

    for t in read_tickers(args):
        for s in sources:
            got, err = ask(t, s)
            if got is None:
                failed.append((t, s, err))
            else:
                recs.extend(got)
    # A portal that throttles us fails several tickers IN A ROW (observed: TCB, TCX, VCB
    # on the first top-50 run). Wait, then retry only those; cached answers cost nothing.
    for attempt, pause in enumerate((30, 120), 1):
        if not failed:
            break
        print(f"retry {attempt}: {len(failed)} ticker/source pairs failed, waiting {pause}s")
        time.sleep(pause)
        again = []
        for t, s, _ in failed:
            if isinstance(s, VietstockSource):
                s._token.pop(t, None)                           # fresh token + cookie
            got, err = ask(t, s)
            if got is None:
                again.append((t, s, err))
            else:
                recs.extend(got)
        failed = again
    _write_csv(out / "discovery_failures.csv",
               [dict(ticker=t, source=s.name, error=e) for t, s, e in failed],
               ["ticker", "source", "error"])
    if failed:
        print(f"WARNING: still failing after retries: {[(t, s.name) for t, s, _ in failed]} "
              f"-> discovery_failures.csv; run `discover` again later")
    with open(out / "discovery.jsonl", "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
    print(f"discovery: {len(recs)} records -> {out / 'discovery.jsonl'}")
    return recs


def load_discovery(out):
    with open(Path(out) / "discovery.jsonl", encoding="utf-8") as fh:
        # re-classify: a fix to titles.py applies without asking the websites again
        return [classify(DocRecord.from_dict(json.loads(ln))) for ln in fh if ln.strip()]


def stage_plan(args, recs=None):
    out = Path(args.out)
    recs = recs if recs is not None else load_discovery(out)
    pby = parse_periods_by_year(args.periods_by_year) if args.periods_by_year else None
    if args.profile == "A":
        # the course's full corpus as proposed after spikes S1/O1/O2 (28/09/2026):
        # one report per company per period (consolidated, or the only one there is),
        # quarters + years for 2024-2026, annual reports only for 2020-2023
        pby = pby or parse_periods_by_year("2020-2023:FY;2024-2030:Q,FY")
        args.scopes = "hopnhat,standalone"
    kept, dropped = select(recs, years=parse_years(args.years),
                           period_types=tuple(args.periods.split(",")),
                           scopes=tuple(args.scopes.split(",")),
                           kinds=tuple(args.kinds.split(",")), langs=tuple(args.langs.split(",")),
                           periods_by_year=pby)
    plan = build_plan(kept, priority=tuple(args.priority.split(",")))
    if args.only_latest:
        plan = [p for p in plan if p["is_latest"]]
    _write_csv(out / "plan.csv", plan)
    _write_csv(out / "dropped.csv",
               [dict(reason=why, source=r.source, ticker=r.ticker, title=r.title, url=r.url)
                for r, why in dropped], ["reason", "source", "ticker", "title", "url"])
    reasons = Counter(why for _, why in dropped)
    print(f"plan: {len(plan)} documents from {len(kept)} records "
          f"(dropped {len(dropped)}: {dict(reasons.most_common(6))})")
    return plan


def sample_plan(plan, k):
    """k documents per ticker for a spike: VARIETY, not volume.

    Tickers are offset against each other so that, across the whole sample, every
    year, every period type (FY / Q / H1) and both scopes show up — an estimate of
    pages or scan share built from annual reports only would be biased (they are
    ~1.5x longer than quarterly ones)."""
    by_t = {}
    for r in plan:
        if str(r["is_latest"]) in ("True", "1"):
            by_t.setdefault(r["ticker"], []).append(r)
    kinds = [("FY", "hopnhat"), ("Q", "hopnhat"), ("H1", "hopnhat"), ("Q", "rieng"),
             ("FY", "rieng"), ("H1", "rieng")]
    out = []
    for j, (t, rows) in enumerate(sorted(by_t.items())):
        years = sorted({int(r["year"]) for r in rows})
        used = set()
        for i in range(k):
            y = years[(j + i * max(1, len(years) // k)) % len(years)]
            pt, sc = kinds[(j + i) % len(kinds)]
            def score(r, y=y, pt=pt, sc=sc):
                scope_ok = r["scope"] in (sc, "standalone")     # no subsidiaries: one report
                return (int(r["year"]) != y, r["period_type"] != pt, not scope_ok,
                        abs(int(r["year"]) - y), r["doc_id"])
            cands = sorted((r for r in rows if r["doc_id"] not in used), key=score)
            if cands:
                used.add(cands[0]["doc_id"])
                out.append(cands[0])
    return out


def stage_download(args, client, plan=None):
    plan = plan if plan is not None else _read_csv(Path(args.out) / "plan.csv")
    if args.sample_per_ticker:
        plan = sample_plan(plan, args.sample_per_ticker)
        print(f"download: SAMPLE of {len(plan)} documents "
              f"({args.sample_per_ticker} per ticker, spread over years)")
    log = download_all(plan, client, args.out, only_latest=args.only_latest,
                       max_docs=args.max_docs)
    st = Counter(r["status"] for r in log.values())
    print(f"download: {dict(st)}")
    return log


def stage_ingest(args):
    out = Path(args.out)
    plan = _read_csv(out / "plan.csv")
    log = read_log(out / "download_log.csv")
    rows, events = ingest(plan, log, out)
    print(f"ingest: {len(rows)} PDFs in corpus/, {len(events)} events "
          f"({dict(Counter(e['event'].split(':')[0] for e in events))})")
    return rows


def stage_reference(args, client):
    build_reference(client, read_tickers(args), parse_years(args.years), args.out)


def stage_report(args):
    from .report import write_report
    p = write_report(args.out)
    print(f"report: {p}")


def stage_merge(args):
    from .merge import merge_packs
    merge_packs([p.strip() for p in args.inputs.split(",") if p.strip()], args.out)


def _ocr_hint(out):
    from .ocr import needs_ocr
    cat = _read_csv(Path(out) / "catalog.csv")
    need = [r for r in cat if needs_ocr(r)]
    if need:
        pages = sum(int(r["pages"] or 0) for r in need)
        print(f"\n{len(need)}/{len(cat)} corpus files ({pages} pages) have no usable Vietnamese "
              f"text layer.\nNext: python -m bctc_collector ocr --out {out} --jobs 4")


# --------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(prog="bctc_collector", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["run", "discover", "plan", "download", "ingest",
                                      "reference", "report", "ocr", "merge", "bench"])
    ap.add_argument("--tickers", help="comma separated, e.g. VNM,FPT,HPG")
    ap.add_argument("--tickers-file", help=".txt (one per line) or .csv (column ticker)")
    ap.add_argument("--years", default="2023-2025", help="e.g. 2024 or 2022-2025 or 2023,2025")
    ap.add_argument("--periods", default="Q,H1,FY", help="subset of Q,H1,9M,FY")
    ap.add_argument("--scopes", default="hopnhat,rieng,standalone")
    ap.add_argument("--periods-by-year", help="e.g. '2020-2023:FY;2024-2026:Q,FY' (overrides --periods)")
    ap.add_argument("--profile", choices=["A"], help="A = the course corpus: consolidated/standalone, "
                    "Q+FY for 2024+, FY only for 2020-2023")
    ap.add_argument("--kinds", default="full", help="full[,partial,explanation]")
    ap.add_argument("--langs", default="vi", help="vi[,en]")
    ap.add_argument("--sources", default="vietstock,cafef", help="vietstock,cafef[,ir]")
    ap.add_argument("--priority", default="vietstock,cafef,ir",
                    help="preferred source order when both have a PDF")
    ap.add_argument("--ir-map", help="CSV ticker,ir_url for the experimental 'ir' source")
    ap.add_argument("--only-latest", action="store_true",
                    help="skip superseded versions of restated reports")
    ap.add_argument("--max-docs", type=int, help="download at most N documents (smoke test)")
    ap.add_argument("--sample-per-ticker", type=int,
                    help="SPIKE: download only K documents per ticker, spread over the years")
    ap.add_argument("--shard", help="k/n: take part k of n of the ticker list (student groups)")
    ap.add_argument("--inputs", help="merge / bench: comma separated pack folders")
    ap.add_argument("--twin", help="bench: born-digital files to use as digital twins")
    ap.add_argument("--ocr-pages", help="ocr: only these pages, e.g. 1-15 (spike)")
    ap.add_argument("--variant", help="ocr: A/B experiment name -> corpus_ocr_<name>/, own log")
    ap.add_argument("--files", help="ocr: comma separated file names, or 'broken'")
    ap.add_argument("--tessdata", help="ocr: 'best' (download tessdata_best) or a folder with .traineddata")
    ap.add_argument("--recheck", action="store_true",
                    help="ocr: re-run the accuracy checks on OCR'd files, no OCR")
    ap.add_argument("--sample-docs", type=int,
                    help="ocr: pick N varied documents (by kind, OCR layer, producer, ticker)")
    ap.add_argument("--no-reference", action="store_true")
    ap.add_argument("--out", default="pack_real")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests per host")
    ap.add_argument("--timeout", type=float, default=90)
    ap.add_argument("--ocr-lang", default="vie", help="tesseract languages, e.g. vie or vie+eng")
    ap.add_argument("--jobs", type=int, default=4, help="parallel OCR workers")
    args = ap.parse_args(argv)

    offline = ("plan", "ingest", "report", "ocr", "merge", "bench")
    client = None if args.stage in offline else make_client(args)
    if args.stage == "discover":
        stage_discover(args, client)
    elif args.stage == "plan":
        stage_plan(args)
    elif args.stage == "download":
        stage_download(args, client)
    elif args.stage == "ingest":
        stage_ingest(args)
    elif args.stage == "reference":
        stage_reference(args, client)
    elif args.stage == "report":
        stage_report(args)
    elif args.stage == "ocr" and args.recheck:
        from .ocr import recheck
        recheck(args.out)
    elif args.stage == "ocr":
        from .ocr import ocr_corpus
        ocr_corpus(args.out, lang=args.ocr_lang, jobs=args.jobs, max_docs=args.max_docs,
                   pages=args.ocr_pages, sample_docs=args.sample_docs, variant=args.variant,
                   files=args.files, tessdata=args.tessdata)
    elif args.stage == "merge":
        stage_merge(args)
    elif args.stage == "bench":
        from .bench import build_bench
        build_bench([p.strip() for p in args.inputs.split(",") if p.strip()], args.out,
                    twin_docs=args.twin.split(",") if args.twin else None,
                    scan_docs=args.files.split(",") if args.files else None)
    else:
        recs = stage_discover(args, client)
        plan = stage_plan(args, recs)
        stage_download(args, client, plan)
        stage_ingest(args)
        if not args.no_reference:
            stage_reference(args, client)
        stage_report(args)
        _ocr_hint(args.out)
    if client:
        print(f"http: {client.stats}")


if __name__ == "__main__":
    main()
