# Architecture

How the pipeline works internally, and why it is built this way. Read this before changing
the code or adding a source. For how to *run* it, see [USAGE.md](USAGE.md).

- [Design principles](#design-principles)
- [Data flow](#data-flow)
- [The data model: claims vs. verified facts](#the-data-model-claims-vs-verified-facts)
- [Stage 1 — discover](#stage-1--discover)
- [Stage 2 — plan](#stage-2--plan)
- [Stage 3 — download](#stage-3--download)
- [Stage 4 — ingest](#stage-4--ingest)
- [Stage 5 — reference](#stage-5--reference)
- [The polite HTTP client](#the-polite-http-client)
- [Extending: adding a new source](#extending-adding-a-new-source)
- [Module map](#module-map)

## Design principles

1. **Resumable by construction.** Each stage reads the previous stage's files and writes its
   own, so the pipeline is a chain of pure-ish file transforms. Any stage can be re-run in
   isolation; an interrupted run continues by re-running the same command.
2. **Be polite to the portals.** One shared session, a minimum delay between requests to the
   same host, retries with backoff, and an on-disk cache so the same question is never asked
   twice. See [the polite HTTP client](#the-polite-http-client).
3. **A website's claim is a claim until the bytes agree.** Titles and API fields are treated
   as *claims*; the file's own content (cover page, text layer, unit) is what the catalog
   finally trusts. This is the single most important idea in the design.
4. **Bytes decide identity collisions.** When two sources, or two versions, might be the same
   document, the tool does not guess — it downloads each and collapses byte-identical files
   at ingest.
5. **Deterministic offline.** A recorded run (`tests/replay/`) lets the whole pipeline run
   with no network, which is what makes the test suite and CI reliable.

## Data flow

```
                 ┌─────────── sources/ ───────────┐
   tickers ─────►│ vietstock · cafef · ir         │  each returns DocRecord[]
                 └────────────────┬───────────────┘
                                  │  titles.classify() fills structured fields
                                  ▼
   discover  ─────────────► discovery.jsonl        (claims, one JSON per line)
                                  │
   plan      ─ select() + build_plan() ─► plan.csv (what to download, ordered candidates)
                                  │
   download  ─ PoliteClient + magic-byte check ─► raw/ + download_log.csv
                                  │
   ingest    ─ unpack · dedup · probe · verify ─► corpus/ + catalog.csv + companies.csv
                                  │
   reference ─────────────► reference_financials.csv   (structured figures)
```

## The data model: claims vs. verified facts

`models.DocRecord` is one document *as a source describes it*. Its fields split in two:

- **Claimed** by the website: `source`, `ticker`, `title`, `url`, `native_id`,
  `published_at`, `ext`, `exchange`, `api_year`, `api_quarter`, `mirrors`.
- **Derived** by `titles.classify()` from the title/URL/API fields: `year`, `period_type`
  (`Q`/`H1`/`9M`/`FY`), `quarter`, `scope` (`hopnhat`/`rieng`/`standalone`), `audit`
  (`audited`/`reviewed`/`unaudited`), `doc_kind` (`full`/`partial`/`explanation`), `lang`,
  `restated`, plus `parse_ok` and a human-readable `parse_note`.

Nothing here is confirmed against the actual PDF yet — that happens at ingest, which records
where each final fact came from (`meta_source`, `meta_check`).

## Stage 1 — discover

`sources/*` each expose `discover(ticker, years) -> list[DocRecord]`. The CLI calls every
selected source for every ticker, runs each record through `titles.classify()`, and writes
them to `discovery.jsonl`.

`titles.classify()` is deliberately conservative: when a title does not state something, the
field keeps its default and `parse_note` explains the assumption (e.g. "no scope in title ->
standalone"). It also reconciles source-specific quirks — for example CafeF files a reviewed
half-year report as *"quý 2 ... (đã soát xét)"*, which is normalized to `H1`.

A portal that throttles tends to fail several tickers in a row, so `discover` retries the
failed ticker/source pairs after 30 s and 120 s before writing `discovery_failures.csv`.

## Stage 2 — plan

`plan.py` turns "everything the sources know" into "what we will download".

- **Identity is semantic, not byte-level.** A document's identity is
  `(ticker, year, period_type, quarter, scope, audit, doc_kind, lang)`. The same identity is
  usually listed by two sources with two different URLs; those become **candidates** of one
  plan row, ordered by `--priority` (and PDFs before archives).
- **A second record for the same identity from the same source is a new version**, not a
  duplicate — companies re-publish restated reports under the same title. When that happens
  the tool does not try to pair versions across sources (that fails on real data); every
  record becomes its own version, and byte-identical files are collapsed later.

`select()` applies the `--years/--periods/--scopes/--kinds/--langs` filters (and the
per-year `--periods-by-year` / `--profile A` override), returning kept rows and dropped rows
with a reason. `build_plan()` groups the kept rows into plan rows and serializes each row's
candidates as JSON.

## Stage 3 — download

`download.py` fetches the bytes for every plan row, trying candidates in order and, within a
candidate, its URL then its mirrors. Two rules:

- **A 200 is not a document.** Portals answer a missing file with an HTML page or a JSON
  error and status 200 surprisingly often. A download counts only if the first bytes are a
  known magic number (`%PDF`, `PK`, `Rar!`, `7z`); otherwise the tool moves to the next
  candidate.
- **Resume is URL-aware.** A row is skipped only if the logged success came from one of
  *this* row's current URLs and the file still exists, so a re-plan that reassigns names is
  safe.

Each result is appended to `download_log.csv` (status, source, url, bytes, sha256, attempts).

## Stage 4 — ingest

`ingest.py` turns `raw/` into a clean `corpus/`:

1. **Unpack** `.zip`/`.rar` to the PDFs inside. Member names from Vietnamese Windows zip
   tools often arrive as cp437 mojibake and are repaired. A report split into parts, or a
   VN+EN pair, keeps all parts (Vietnamese first, suffixed `__p1`, `__p2`, ...).
2. **De-duplicate** by SHA-1: identical bytes are stored once; the other `doc_id` is logged
   as a duplicate in `ingest_events.csv`.
3. **Probe** each PDF (`probe.py`): page count, text density, and a `text_quality` verdict
   that distinguishes usable Vietnamese text from its look-alikes — `scanned` (no text),
   `no_diacritics` (scanner OCR), `legacy_font` (TCVN3/VNI), `cid_garbage` (no Unicode map),
   `english`, or `ok`. It also detects the reporting unit, the statement form (corporate /
   bank / securities / insurance), and whether the file is digitally signed.
4. **Verify** the cover against the source's claim (`verify_cover`): does the first page
   actually mention that year and scope? Only text the probe trusts (`ok`) is allowed to
   contradict the source, so an undiacritized OCR layer never produces false mismatches.
5. **Catalog**: one row per corpus file, the stable "N01" columns first
   (see [USAGE.md §5](USAGE.md#5-what-the-output-folder-contains)) and provenance columns
   after. `companies.csv` aggregates per ticker (exchange, dominant unit, statement form,
   whether it files consolidated reports).

## Stage 5 — reference

`reference.py` pulls structured financial figures (from the sources' structured endpoints)
into `reference_financials.csv`. These are used downstream as evaluation ground truth — the
"known answers" a RAG system's extraction can be scored against — and to cross-check parse
quality via accounting identities that must hold across a report.

## The polite HTTP client

`http.PoliteClient` is the single choke point for all network I/O:

- **Per-host pacing:** a request to a host waits at least `delay` seconds after the previous
  one (default 1.5 s). This is the difference between "one person reading the site" and a
  denial-of-service burst — see [DATA_SOURCES.md](DATA_SOURCES.md).
- **Retries:** `429/500/502/503/504` are retried with backoff.
- **On-disk cache:** `get_text`/`get_json`/`post_json` cache the response body keyed by
  method + URL + sorted form data. A CSRF token needed only on a cache miss is injected via
  an `on_miss` callback so it never becomes part of the cache key.
- **Streaming downloads:** `stream_to_file` writes atomically (`.part` then rename) and caps
  the size.

`replay.ReplayClient` subclasses it to answer purely from a recorded cache (and
`FaultyClient` injects faults for the fallback tests), which is how the pipeline runs with
no network.

## Extending: adding a new source

A source is a small class with one required method:

```python
class MySource:
    name = "mysource"
    def __init__(self, client):
        self.client = client
    def discover(self, ticker, years) -> list[DocRecord]:
        ...   # query the site via self.client, return DocRecord(...) objects
```

Steps:
1. Put it in `sources/mysource.py` and register it in `sources/__init__.py` (`SOURCES` dict).
2. Fill the **claimed** fields of each `DocRecord`; leave the derived fields to
   `titles.classify()`. Set `mirrors` if the same bytes live on several hosts.
3. Wire it into `cli.stage_discover` (the `--sources` switch) and, if relevant, into
   `--priority`.
4. Add a test: record its answers under a replay fixture, or unit-test its title parsing in
   `tests/test_titles.py`.

Keep all network access going through `self.client` — never call `requests` directly — so
pacing, caching and offline replay keep working.

## Module map

| Module | Responsibility |
|---|---|
| `models.py` | `DocRecord` dataclass (claimed + derived fields) |
| `http.py` | `PoliteClient`: pacing, retries, cache, streaming |
| `titles.py` | `classify()` title → metadata; `canonical_name()`, `period_str()` |
| `sources/` | `vietstock.py`, `cafef.py`, `ir_site.py` + the `SOURCES` registry |
| `plan.py` | `select()` filters, `build_plan()` identity grouping & versioning |
| `download.py` | `download_all()`: candidates, mirrors, magic-byte check, resume |
| `probe.py` | `probe_pdf()`, `text_quality()`, `verify_cover()` |
| `ingest.py` | `ingest()`: unpack, dedup, probe, verify, write catalog |
| `reference.py` | `build_reference()`: structured figures |
| `report.py` | `write_report()`: one-page pack summary |
| `ocr.py`, `ocr_eval.py` | optional Vietnamese OCR + accuracy checks |
| `merge.py`, `bench.py` | combine packs / OCR benchmark |
| `cli.py` | argument parsing and stage orchestration |
| `replay.py` | `ReplayClient` / `FaultyClient` for offline runs and tests |
