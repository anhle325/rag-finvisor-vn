"""Vietstock Finance — https://finance.vietstock.vn/<TICKER>/tai-tai-lieu.htm

How the page works (inspected Sept 2026, anonymous session, no login):

1. GET  /<TICKER>/tai-tai-lieu.htm?doctype=1
       -> HTML with a hidden anti-forgery token
          <input name=__RequestVerificationToken type=hidden value=...>
          plus a matching cookie set on the session.
2. POST /data/getyearreport   {code}                       -> [2026, 2025, ...]
3. POST /data/getdocument     {code, page, type=1, year, __RequestVerificationToken}
       -> [{Title, FullName, Url, FileExt, FileInfoID, LastUpdate, TotalRow}, ...]
          20 rows per page; `year` is REQUIRED (year=0 returns nothing).

type = document type: 1 BCTC, 2 annual report, 8 business-result explanations,
9 governance report, 4/5 AGM (from /data/getdocumenttype).

File URLs point at static2.vietstock.vn/data/<EXCHANGE>/<YEAR>/BCTC/VN/<QUY n|NAM>/...
and about a third of them are .zip / .rar archives, not PDFs.
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import quote

from ..models import DocRecord

BASE = "https://finance.vietstock.vn"
RE_TOKEN = re.compile(
    r'name="?__RequestVerificationToken"?\s+type="?hidden"?\s+value="?([^"\s>]+)')
RE_MSDATE = re.compile(r"/Date\((\d+)\)/")
PAGE_SIZE = 20


def _iso_from_msdate(s):
    m = RE_MSDATE.search(s or "")
    if not m:
        return ""
    return dt.datetime.fromtimestamp(int(m.group(1)) / 1000, dt.timezone.utc).isoformat()


def _fix_url(u):
    u = (u or "").strip().replace("http://", "https://", 1)
    # the API returns raw spaces ("QUY 4"); keep the path, quote only what needs it
    return quote(u, safe=":/?=&%.-_~+")


def _exchange_from_url(u):
    m = re.search(r"/data/(HOSE|HNX|UPCOM|OTC)/", u or "", re.I)
    return m.group(1).upper() if m else ""


class VietstockSource:
    name = "vietstock"

    def __init__(self, client, doc_type=1):
        self.client = client
        self.doc_type = doc_type
        self._token = {}

    def _headers(self, ticker):
        return {"X-Requested-With": "XMLHttpRequest",
                "Origin": BASE,
                "Referer": f"{BASE}/{ticker}/tai-tai-lieu.htm?doctype={self.doc_type}",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}

    def token(self, ticker):
        if ticker not in self._token:
            html = self.client.get_text(f"{BASE}/{ticker}/tai-tai-lieu.htm?doctype={self.doc_type}",
                                        use_cache=False)
            m = RE_TOKEN.search(html)
            if not m:
                raise RuntimeError("Vietstock: anti-forgery token not found "
                                   "(page layout changed, or the request was blocked)")
            self._token[ticker] = m.group(1)
        return self._token[ticker]

    def years(self, ticker):
        return self.client.post_json(f"{BASE}/data/getyearreport", {"code": ticker},
                                     headers=self._headers(ticker)) or []

    def _page(self, ticker, year, page):
        data = {"code": ticker, "page": page, "type": self.doc_type, "year": year}
        # the token is fetched only on a cache miss and is not part of the cache key
        return self.client.post_json(
            f"{BASE}/data/getdocument", data, headers=self._headers(ticker),
            on_miss=lambda: {"__RequestVerificationToken": self.token(ticker)}) or []

    def discover(self, ticker, years):
        ticker = ticker.upper()
        available = set(self.years(ticker))
        out = []
        for y in sorted(set(years) & available if available else years):
            page, rows = 1, []
            while True:
                batch = self._page(ticker, y, page)
                rows.extend(batch)
                total = batch[0].get("TotalRow", len(batch)) if batch else 0
                if not batch or len(rows) >= total or len(batch) < PAGE_SIZE:
                    break
                page += 1
            for r in rows:
                url = _fix_url(r.get("Url"))
                out.append(DocRecord(
                    source=self.name, ticker=ticker,
                    title=(r.get("FullName") or r.get("Title") or "").strip(),
                    url=url, native_id=str(r.get("FileInfoID", "")),
                    published_at=_iso_from_msdate(r.get("LastUpdate")),
                    ext=(r.get("FileExt") or "").strip().lower(),
                    exchange=_exchange_from_url(url), api_year=y))
        return out
