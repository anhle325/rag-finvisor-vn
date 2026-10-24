# Data sources & responsible use

Which sites the pipeline reads, how each one is queried, and the rules the tool enforces so
it stays a well-behaved client. Read this before pointing the tool at a new site or raising
its request rate.

- [Overview](#overview)
- [Vietstock Finance](#vietstock-finance)
- [CafeF](#cafef)
- [Company IR pages (experimental)](#company-ir-pages-experimental)
- [Politeness rules the tool enforces](#politeness-rules-the-tool-enforces)
- [Legal & ethical use](#legal--ethical-use)
- [Secrets](#secrets)

## Overview

The documents are **public disclosures**: Vietnamese listed companies are required to
publish their financial statements, and the data aggregators below republish them. The tool
reads two aggregators plus, optionally, a company's own investor-relations page. All access
goes through one `PoliteClient` (see [ARCHITECTURE.md](ARCHITECTURE.md)); endpoint details
below were inspected in September 2026 and may change.

## Vietstock Finance

`https://finance.vietstock.vn/<TICKER>/tai-tai-lieu.htm` — the default primary source.

How a document list is obtained (anonymous session, no login):

1. `GET /<TICKER>/tai-tai-lieu.htm?doctype=1` returns HTML carrying a hidden anti-forgery
   token (`__RequestVerificationToken`) and a matching session cookie.
2. `POST /data/getyearreport {code}` → the list of years available.
3. `POST /data/getdocument {code, page, type=1, year, __RequestVerificationToken}` → 20 rows
   per page; `year` is required.

`type` selects the document family (1 = BCTC/financial statements). File URLs point at
`static2.vietstock.vn/...`, and roughly a third are `.zip`/`.rar` archives rather than PDFs.
The token is fetched only on a cache miss and is never part of the cache key (handled by the
`on_miss` callback in `PoliteClient.post_json`).

## CafeF

`https://cafef.vn/du-lieu/...` — the default fallback source.

The document tab is backed by one plain JSON endpoint (no token, no login):

```
GET https://cafef.vn/du-lieu/Ajax/PageNew/FileBCTC.ashx?Symbol=VNM&Type=1&Year=0
-> {"Data": [{id, Quarter, Year, Time, Name, Link}, ...], "Success": true}
```

Notes the source adapter relies on:
- `Type` 1 = financial statements; `Year=0` returns the whole history in one call.
- `Quarter` 5 means the full year.
- `id` is a MongoDB ObjectId whose first 8 hex digits encode the creation time — a free,
  reliable `published_at` for ordering restatements.
- Files live on a CDN with three host names; a link can be dead on one and alive on another,
  so every CafeF record carries the other two hosts as **mirrors** (used by the download
  fallback).

## Company IR pages (experimental)

The `ir` source (off by default) fetches **one** HTML page per ticker from a `ticker,ir_url`
map and keeps the links that point at a document and look like a financial statement. It is
the most defensible source (the company publishes its own disclosure) but the least uniform,
and many IR pages render their document list with JavaScript, which a plain HTTP client never
sees. Use it to fill gaps, not as the primary source; a headless-browser version is
deliberately out of scope here.

## Politeness rules the tool enforces

Two rules are built into `http.PoliteClient` and must not be worked around:

1. **Never hammer a portal.** Every request to the same host waits at least `--delay`
   seconds (default 1.5 s). Consider the arithmetic: 40 people × 30 tickers × 3 years with no
   delay is a denial-of-service burst; with the delay it is the traffic of one person reading
   the site. Lower `--delay` only with good reason, and never set it to 0 against a live
   portal.
2. **Never ask twice.** API answers are cached on disk; re-running a stage re-reads the
   cache, not the website.

Retries use exponential backoff and stop after a few attempts, so a struggling server is not
pounded.

## Legal & ethical use

This tool is for research and education on **publicly disclosed** filings. Keep it that way:

- **Respect each site.** Check `robots.txt` and the site's terms of use; keep the request
  rate gentle; stop if a site asks you to.
- **Prefer the official source** when you can — a company's own disclosure or the exchange —
  over a third-party scrape.
- **Do not redistribute** copyrighted PDFs unless you have confirmed the terms. The intended
  workflow is that each user builds their *own* corpus from these commands; the repository
  ships only a tiny recorded sample for tests, not a redistributed archive.
- **Personal/sensitive data:** financial statements are corporate disclosures, but apply the
  same care you would to any scraped data and do not combine it to profile individuals.

This document is practical guidance, not legal advice; when in doubt about a specific site or
dataset, get sign-off from whoever owns that decision in your organization.

## Secrets

- **Never commit API keys or tokens.** `.gitignore` already blocks `.env` and `*.key`, and
  the pipeline reads any key it needs from an environment variable — never from a file in the
  repository.
- The core pipeline (`discover → plan → download → ingest → reference`) needs **no** API key;
  only an optional LLM-assisted step elsewhere in the system would, and that reads
  `OPENAI_API_KEY` (or the relevant variable) from the environment.
