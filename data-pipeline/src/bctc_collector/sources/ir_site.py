"""Company investor-relations pages ("Quan hệ cổ đông") — EXPERIMENTAL.

The most defensible source (the company publishes its own disclosure), and the
least uniform: every company builds its IR page differently, and many render the
document list with JavaScript, which a plain HTTP client never sees.

This source does the simple thing well: fetch one HTML page per ticker, keep the
links that point at a document (.pdf/.zip/.rar) AND look like a financial
statement (by link text or file name). Use it to fill gaps the aggregators
leave, not as the primary source. For JS-rendered IR pages, a headless browser
(Playwright) is the next step — deliberately out of scope here.

Input: a CSV with columns `ticker,ir_url` (e.g. the MIT-licensed company list of
github.com/Tumiqa/vn-annual-report-miner has an IR-portal column).
"""

from __future__ import annotations

import html as htmllib
import os
import re
from urllib.parse import urljoin, urlparse, unquote

from ..models import DocRecord
from ..titles import strip_accents

RE_A = re.compile(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
RE_TAG = re.compile(r"<[^>]+>")
DOC_EXT = (".pdf", ".zip", ".rar")
KEYWORDS = ("bctc", "bao cao tai chinh", "baocaotaichinh", "bao-cao-tai-chinh",
            "financial statement", "financial report")


class IRSiteSource:
    name = "ir"

    def __init__(self, client, ir_map):
        self.client = client
        self.ir_map = {k.upper(): v for k, v in (ir_map or {}).items() if v}

    def discover(self, ticker, years):
        ticker = ticker.upper()
        page = self.ir_map.get(ticker)
        if not page:
            return []
        try:
            html = self.client.get_text(page)
        except Exception:                                       # noqa: BLE001
            return []
        out, seen = [], set()
        for href, inner in RE_A.findall(html):
            url = urljoin(page, htmllib.unescape(href.strip()))
            ext = os.path.splitext(urlparse(url).path)[1].lower()
            if ext not in DOC_EXT or url in seen:
                continue
            text = re.sub(r"\s+", " ", htmllib.unescape(RE_TAG.sub(" ", inner))).strip()
            fname = unquote(os.path.basename(urlparse(url).path))
            probe = strip_accents((text + " " + fname).lower()).replace("_", " ")
            if not any(k in probe for k in KEYWORDS):
                continue
            seen.add(url)
            out.append(DocRecord(source=self.name, ticker=ticker,
                                 title=text or fname, url=url, ext=ext))
        ys = set(years)
        return [r for r in out if not ys or any(str(y) in r.title + r.url for y in ys)]
