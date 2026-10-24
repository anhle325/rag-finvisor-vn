"""CafeF — https://cafef.vn/du-lieu/<san>/<ticker>-tai-lieu.chn

The document tab is filled by a plain JSON endpoint (inspected Sept 2026, no
token, no login):

    GET https://cafef.vn/du-lieu/Ajax/PageNew/FileBCTC.ashx?Symbol=VNM&Type=1&Year=0
    -> {"Data": [{id, Quarter, Year, Time, Name, Link}, ...], "Success": true}

Type 1 = BCTC, 3 = annual report / charter, 4 = AGM & board resolutions,
5 = governance reports. Year=0 returns the whole history in one call
(VNM: 217 files back to 2005). Quarter 5 means the full year ("CN/2025").

`id` is a MongoDB ObjectId: its first 8 hex digits are the Unix time the record
was created — a free, reliable "published_at" for ordering restatements.

Files live on a CDN with three host names. A link can be dead on one host and
alive on another (observed: cafefnew.mediacdn.vn -> 404, cafef1.mediacdn.vn ->
200 for the same path), so every CafeF record carries the other two as mirrors.
"""

from __future__ import annotations

import datetime as dt
import os
from urllib.parse import urlparse

from ..models import DocRecord

BASE = "https://cafef.vn"
CDN_HOSTS = ("cafef1.mediacdn.vn", "cafefnew.mediacdn.vn", "cafef.mediacdn.vn")


def objectid_time(oid):
    try:
        return dt.datetime.fromtimestamp(int(oid[:8], 16), dt.timezone.utc).isoformat()
    except (ValueError, TypeError):
        return ""


def cdn_mirrors(url):
    p = urlparse(url)
    if p.netloc not in CDN_HOSTS:
        return []
    return [p._replace(netloc=h).geturl() for h in CDN_HOSTS if h != p.netloc]


class CafefSource:
    name = "cafef"

    def __init__(self, client, doc_type=1):
        self.client = client
        self.doc_type = doc_type

    def raw(self, ticker, year=0):
        url = (f"{BASE}/du-lieu/Ajax/PageNew/FileBCTC.ashx?"
               f"Symbol={ticker.lower()}&Type={self.doc_type}&Year={year}")
        return self.client.get_json(url, headers={"Referer": f"{BASE}/"})

    def discover(self, ticker, years):
        ticker = ticker.upper()
        j = self.raw(ticker, 0) or {}
        years = set(years)
        out = []
        for r in j.get("Data") or []:
            if years and r.get("Year") not in years:
                continue
            link = (r.get("Link") or "").strip()
            ext = os.path.splitext(urlparse(link).path)[1].lower()
            out.append(DocRecord(
                source=self.name, ticker=ticker, title=(r.get("Name") or "").strip(),
                url=link, native_id=str(r.get("id", "")),
                published_at=objectid_time(r.get("id", "")), ext=ext,
                api_year=r.get("Year"), api_quarter=r.get("Quarter"),
                mirrors=cdn_mirrors(link)))
        return out
