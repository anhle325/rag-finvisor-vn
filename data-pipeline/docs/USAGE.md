# Usage guide

How to install the pipeline and run it, stage by stage, with the full list of options, the
offline replay mode, the shape of the output, and a troubleshooting section.

- [1. Requirements](#1-requirements)
- [2. Installation](#2-installation)
- [3. The mental model: five resumable stages](#3-the-mental-model-five-resumable-stages)
- [4. The usual entry point: `run`](#4-the-usual-entry-point-run)
- [5. What the output folder contains](#5-what-the-output-folder-contains)
- [6. Running stages individually](#6-running-stages-individually)
- [7. Command-line options](#7-command-line-options)
- [8. Resuming and caching](#8-resuming-and-caching)
- [9. Offline mode (replay)](#9-offline-mode-replay)
- [10. Optional: OCR for scanned reports](#10-optional-ocr-for-scanned-reports)
- [11. Feeding the output to the rest of the system](#11-feeding-the-output-to-the-rest-of-the-system)
- [12. Troubleshooting](#12-troubleshooting)

---

## 1. Requirements

- Python **3.9 or newer**.
- Network access to the disclosure portals for a live run (`discover`, `download`,
  `reference`). The `plan`, `ingest`, `report` and `ocr` stages are fully offline.
- Optional, only to extract `.rar` archives some portals return: one of `unar`, `7z` or
  `unrar` on the system `PATH` (macOS `tar` usually works too), and optionally the
  `rarfile` Python package (`pip install -e .[rar]`).
- Optional, only for the OCR stage: `ocrmypdf` and Tesseract with the Vietnamese language
  data (`vie`).

## 2. Installation

All commands below assume you are inside the `data-pipeline/` folder of the repository.

```bash
cd data-pipeline
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# Editable install with the dev extras (pytest + pandas):
pip install -e ".[dev]"

# Verify:
pytest                               # 16 tests, fully offline
python -m bctc_collector --help
```

If you only want to run the tool (no development), `pip install -r requirements.txt` is
enough, and you then run it as `python -m bctc_collector ...`.

## 3. The mental model: five resumable stages

```
tickers ──► discover ──► plan ──► download ──► ingest ──► reference
```

| Stage | Reads | Writes | In one line |
|---|---|---|---|
| `discover` | the tickers | `discovery.jsonl` | Ask every source which documents exist for each ticker. |
| `plan` | `discovery.jsonl` | `plan.csv`, `dropped.csv` | Decide which documents the corpus should contain, and from which source. |
| `download` | `plan.csv` | `raw/`, `download_log.csv` | Fetch the bytes, with fallbacks, validating that each file really is a document. |
| `ingest` | `raw/` + `plan.csv` | `corpus/`, `catalog.csv`, `companies.csv` | Unpack, de-duplicate, inspect and catalog every PDF. |
| `reference` | the tickers | `reference_financials.csv` | Pull structured figures, used later as evaluation ground truth. |

Each stage writes its output and stops. Because a stage only reads the previous stage's
files, you can stop after any stage, inspect the files, and resume — nothing is recomputed
or re-fetched unnecessarily (see [§8](#8-resuming-and-caching)).

Two further stages are **optional** and **not** part of `run`: `ocr` (add a Vietnamese text
layer to scanned PDFs — can take hours) and `merge` (combine packs built by several people).
`report` writes a one-page summary of a pack.

## 4. The usual entry point: `run`

`run` executes `discover → plan → download → ingest → reference → report` in order:

```bash
python -m bctc_collector run --tickers VNM,FPT,HPG --years 2023-2025 --out pack_real
```

A larger, more controlled run from a ticker file:

```bash
python -m bctc_collector run \
    --tickers-file examples/tickers_top50.txt \
    --years 2024 --periods Q,FY --scopes hopnhat \
    --only-latest --out pack_vn
```

Split the work across a team of 10 groups (each group builds its own shard), then merge:

```bash
# group 3 of 10 (round-robin, so every group gets a mix of banks and non-banks)
python -m bctc_collector run --tickers-file examples/tickers_top50.txt \
    --shard 3/10 --years 2020-2026 --out pack_g03
python -m bctc_collector merge --inputs pack_g01,pack_g02,pack_g03,... --out pack_all
```

## 5. What the output folder contains

After a full `run --out pack_real`:

```
pack_real/
├── discovery.jsonl          # every document each source reported (one JSON per line)
├── discovery_failures.csv   # ticker/source pairs that failed after retries (usually empty)
├── plan.csv                 # one row per document to download, with ordered candidates
├── dropped.csv              # documents excluded by the filters, with the reason
├── raw/<TICKER>/            # the exact bytes downloaded (pdf/zip/rar), before processing
├── download_log.csv         # per-document download result (status, source, url, sha256...)
├── corpus/*.pdf             # the clean, de-duplicated PDFs — the deliverable
├── catalog.csv              # one row per corpus file (see columns below)
├── companies.csv            # one row per ticker (exchange, reporting unit, form, ...)
├── ingest_events.csv        # notable events: duplicates, cover mismatches, unpack failures
├── reference_financials.csv # structured figures for evaluation
├── run_report.md            # one-page summary of this pack
└── _http_cache/             # cached API answers (so re-runs don't re-ask the portals)
```

**`catalog.csv`** is the table the downstream RAG components read. Its leading columns are:

| Column | Meaning |
|---|---|
| `file` | file name inside `corpus/` |
| `ticker`, `year`, `quarter`, `period` | identity of the report (`period` e.g. `2024Q3`, `2025H1`, `2024`) |
| `scope` | `hopnhat` (consolidated) or `rieng` (standalone / parent-only) |
| `audited` | boolean |
| `unit` | reporting unit detected inside the PDF (`dong`, `nghin`, `trieu`, `ty`) |
| `pages`, `chars_per_page` | size and text density |
| `kind` | `digital`, `mixed`, `scanned` (how much usable text the PDF has) |
| `sha1` | content hash (identical files are stored once) |
| `meta_source` | `source+cover` if the PDF's cover confirmed the source's claim, else `source` |

More columns follow (text-quality ratios, provenance URL, version flags, signature,
statement form). See [ARCHITECTURE.md](ARCHITECTURE.md) for what each one means.

## 6. Running stages individually

Useful to inspect the result before committing to the next step, or to re-run one stage
after changing the code or the filters.

```bash
python -m bctc_collector discover --tickers SHS --years 2025 --out tmp   # look first
python -m bctc_collector plan     --years 2025 --out tmp                 # offline, re-reads discovery
python -m bctc_collector download --out tmp
python -m bctc_collector ingest   --out tmp                              # offline
python -m bctc_collector reference --tickers SHS --years 2025 --out tmp
python -m bctc_collector report   --out tmp                              # offline
```

Because `plan` re-reads `discovery.jsonl` and re-classifies every record, a fix to the title
parser takes effect on `plan` alone — **without asking the websites again**.

## 7. Command-line options

Run `python -m bctc_collector --help` for the authoritative list. The ones you will use most:

**Selecting what to collect**

| Option | Default | Meaning |
|---|---|---|
| `--tickers` | — | comma-separated, e.g. `VNM,FPT,HPG` |
| `--tickers-file` | — | a `.txt` (one ticker per line, `#` comments allowed) or `.csv` (a `ticker`/`symbol`/`mã ck`/`code` column) |
| `--years` | `2023-2025` | `2024`, or a range `2022-2025`, or a list `2023,2025` |
| `--periods` | `Q,H1,FY` | subset of `Q,H1,9M,FY` |
| `--scopes` | `hopnhat,rieng,standalone` | which report scopes to keep |
| `--periods-by-year` | — | per-year override, e.g. `'2020-2023:FY;2024-2026:Q,FY'` |
| `--profile A` | — | the ready-made "course corpus" profile: consolidated/standalone, Q+FY from 2024 on, annual only for 2020-2023 |
| `--kinds` | `full` | `full[,partial,explanation]` — `full` keeps whole reports only |
| `--langs` | `vi` | `vi[,en]` |
| `--only-latest` | off | skip superseded versions of restated reports |

**Sources and ordering**

| Option | Default | Meaning |
|---|---|---|
| `--sources` | `vietstock,cafef` | which sources to query (`ir` is experimental) |
| `--priority` | `vietstock,cafef,ir` | preferred source order when more than one has a PDF |
| `--ir-map` | — | CSV `ticker,ir_url` for the experimental `ir` source |

**Scale, sampling, politeness**

| Option | Default | Meaning |
|---|---|---|
| `--sample-per-ticker` | — | spike: download only K varied documents per ticker (variety, not volume) |
| `--shard` | — | `k/n`: take part k of n of the ticker list (team splitting) |
| `--max-docs` | — | download at most N documents (smoke test) |
| `--delay` | `1.5` | seconds to wait between requests **to the same host** |
| `--timeout` | `90` | per-request timeout in seconds |
| `--no-reference` | off | skip the `reference` stage inside `run` |
| `--out` | `pack_real` | output folder |

## 8. Resuming and caching

Two independent mechanisms make re-runs cheap and safe:

1. **API cache (`_http_cache/`).** Every small JSON answer from a portal is cached on disk,
   keyed by URL + request body. `discover` and `reference` read the cache before the network,
   so running them again costs nothing and returns the same data.
2. **Download resume (`download_log.csv`).** A document already downloaded successfully is
   skipped on the next run — but only if the file on disk came from one of the current plan
   row's URLs, so re-planning can safely reassign names.

As a result, if a run is interrupted (network drop, throttling, Ctrl-C), just run the same
command again: it continues where it stopped.

## 9. Offline mode (replay)

A full live run on VNM + SHS (27 Sep 2026) was recorded into `tests/replay/`. `ReplayClient`
answers from that recording and never opens a socket — this is what the test suite and CI
use, and it is the way to demo the pipeline without network access.

```python
from bctc_collector.replay import ReplayClient
from bctc_collector.sources import VietstockSource, CafefSource
from bctc_collector.titles import classify
from bctc_collector.plan import select, build_plan

client = ReplayClient("tests/replay")
recs = []
for Src in (VietstockSource, CafefSource):
    for ticker in ("VNM", "SHS"):
        recs += [classify(r) for r in Src(client).discover(ticker, [2025])]

kept, _ = select(recs, years=[2025])
plan = build_plan(kept)
print(len(plan), "documents planned —", client.stats)   # 0 network requests
```

Only two small PDFs were recorded, so a replay `download` succeeds for those two documents
and returns HTTP 404 for every other URL — enough to exercise every stage end to end.
`FaultyClient` (same module) injects dead hosts and HTML-200 responses to test the download
fallbacks offline.

## 10. Optional: OCR for scanned reports

Some filings are scans with no usable Vietnamese text layer. `ingest` flags them
(`kind = scanned` / `text_quality` not `ok`), and the end of a `run` prints how many need
OCR. The OCR stage is separate because it is slow:

```bash
# spike: OCR 6 varied documents, first 15 pages each
python -m bctc_collector ocr --out pack_real --sample-docs 6 --ocr-pages 1-15
# full OCR with 4 workers
python -m bctc_collector ocr --out pack_real --jobs 4
```

It needs `ocrmypdf` + Tesseract `vie`; it writes `corpus_ocr/`, `ocr_log.csv` and
`ocr_report.md`, and re-running `ingest` folds the OCR results back into `catalog.csv`.

## 11. Feeding the output to the rest of the system

The output folder has the same shape the downstream components expect. Point them at it:

```bash
export RAG_PACK=/path/to/pack_real
```

The corpus (`corpus/`), the catalog (`catalog.csv`) and the structured figures
(`reference_financials.csv`) are the three artifacts the retrieval and evaluation components
consume.

## 12. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `discovery_failures.csv` is non-empty | A portal throttled or blocked some ticker/source pairs. `discover` already retries after 30 s and 120 s; run `discover` again later — cached answers cost nothing, so only the missing ones are re-fetched. |
| `Vietstock: anti-forgery token not found` | The Vietstock page layout changed, or the request was blocked. Re-run later; if it persists, the token regex in `sources/vietstock.py` needs updating. |
| `cannot extract archive ...` | A `.zip`/`.rar` could not be unpacked. Install one of `unar`, `7z`, `unrar` (macOS `tar` usually works), or `pip install -e .[rar]`. |
| Many files show `kind = scanned` | Those PDFs are image scans. Run the optional OCR stage ([§10](#10-optional-ocr-for-scanned-reports)). |
| `not_a_document` in `download_log.csv` | The server answered 200 with an HTML error page or a JSON error; the magic-byte check refused it and tried the next candidate. If every candidate fails, the file is genuinely unavailable right now. |
| `cover_mismatch:...` in `ingest_events.csv` | The PDF's cover page did not confirm the year/scope the source claimed. Inspect that file; it may be mislabeled by the source. |
| A live run is slow | That is the politeness delay doing its job. Lower `--delay` cautiously; never set it to 0 against a real portal. |
