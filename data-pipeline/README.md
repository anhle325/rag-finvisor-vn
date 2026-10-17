# data-pipeline — BCTC corpus builder

**Build a corpus of real Vietnamese listed-company financial statements (BCTC) from a list
of stock tickers.**

Given a few ticker symbols, this tool turns one command

```bash
python -m bctc_collector run --tickers VNM,FPT,HPG --years 2023-2025 --out pack_real
```

into a folder `pack_real/` containing the clean PDFs plus a catalog and a structured
reference table — ready to be chunked, embedded and indexed by the rest of the
`rag-finvisor-vn` system.

```
tickers ──► discover ──► plan ──► download ──► ingest ──► reference
            (what do     (one row  (fetch bytes  (unpack,    (structured
             the sources   per doc   + fallbacks)  probe,      figures for
             list?)        identity)               verify)     evaluation)
```

Every stage writes its result to disk and stops, so each stage is **resumable** and a
re-run never re-asks the websites.

## Documentation

| Read this | For |
|---|---|
| **[docs/USAGE.md](docs/USAGE.md)** | Install, run every stage, all flags, offline mode, output format, resuming, troubleshooting. **Start here.** |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the pipeline works internally — the data model, each stage, the design decisions — for anyone modifying the code. |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | The sources (Vietstock, CafeF, company IR), how each one is queried, and the legal / ethical rules the tool enforces. |

## Quick start

```bash
cd data-pipeline
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .[dev]
pytest                                               # 16 tests, fully offline
python -m bctc_collector --help
```

See [docs/USAGE.md](docs/USAGE.md) for a live run and for the offline replay mode used in
tests and CI.

## License

MIT — see [LICENSE](LICENSE).
