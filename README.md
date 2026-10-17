# rag-finvisor-vn

A Retrieval-Augmented Generation (RAG) system for **question answering over the financial
statements of Vietnamese listed companies**.

The system is built as independent components. The first one lives here:

| Component | Path | What it does |
|---|---|---|
| **Data pipeline** | [`data-pipeline/`](data-pipeline/) | Builds a corpus of **real** financial statements (BCTC) from a list of stock tickers: it discovers, downloads, verifies and catalogs the PDFs that later components index and answer from. |

Start with **[`data-pipeline/README.md`](data-pipeline/README.md)**, then the detailed guides
under [`data-pipeline/docs/`](data-pipeline/docs/).

## Repository layout

```
rag-finvisor-vn/
├── data-pipeline/          # component 1 — BCTC corpus builder (this release)
│   ├── src/bctc_collector/ # the pipeline package
│   ├── tests/              # offline test suite
│   ├── docs/               # usage, architecture, data-source guides
│   └── README.md
├── .github/workflows/      # CI: runs the data-pipeline tests on every push / PR
└── LICENSE                 # MIT
```

Further components (retrieval, evaluation, serving) will be added as sibling folders.

## License

MIT — see [`LICENSE`](LICENSE).
