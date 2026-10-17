"""From 'everything the sources know' to 'what we will download'.

Two ideas, both taught in N01 and needed here for real:

* **Identity is semantic, not byte-level.** A document is identified by
  (ticker, period, scope, audit status, kind, language) — the same identity is
  typically listed by two sources with two different URLs.
* **A second record for the same identity from the SAME source is a new
  version**, not a duplicate: companies re-publish restated reports under the
  same title (observed: SHS Q4/2025, two CafeF records, the second one named
  "...Dieu_chinh_Bao_cao_tai_chinh_quy_4_2025..."). When that happens we do not
  guess which record of the other source matches which version: every record
  is downloaded as its own version and the bytes decide (identical files are
  collapsed at ingest).
"""

from __future__ import annotations

import json
from collections import defaultdict

from .titles import canonical_name, period_str

IDENTITY = ("ticker", "year", "period_type", "quarter", "scope", "audit", "doc_kind", "lang")
DEFAULT_PRIORITY = ("vietstock", "cafef", "ir")


def identity(rec):
    return tuple(getattr(rec, k) for k in IDENTITY)


def parse_periods_by_year(spec):
    """'2020-2023:FY;2024-2026:Q,FY' -> {2020: {'FY'}, ..., 2024: {'Q', 'FY'}, ...}"""
    out = {}
    for part in (spec or "").split(";"):
        if not part.strip():
            continue
        yrs, _, pts = part.partition(":")
        a, _, b = yrs.strip().partition("-")
        for y in range(int(a), int(b or a) + 1):
            out[y] = {p.strip() for p in pts.split(",") if p.strip()}
    return out


def select(records, years=None, period_types=("Q", "H1", "FY"),
           scopes=("hopnhat", "rieng", "standalone"), kinds=("full",), langs=("vi",),
           periods_by_year=None):
    """Keep what the corpus should contain. Returns (kept, dropped_with_reason).

    periods_by_year (optional) overrides period_types per year, e.g. the course's
    'profile A': {2020..2023: {'FY'}, 2024..2026: {'Q', 'FY'}}."""
    kept, dropped = [], []
    ys = set(years or [])
    for r in records:
        why = None
        if not r.parse_ok:
            why = "unparsed_title"
        elif ys and r.year not in ys:
            why = "year"
        elif r.period_type not in (periods_by_year.get(r.year, set()) if periods_by_year
                                   else period_types):
            why = f"period_{r.period_type}"
        elif r.scope not in scopes:
            why = f"scope_{r.scope}"
        elif r.doc_kind not in kinds:
            why = f"kind_{r.doc_kind}"
        elif r.lang not in langs:
            why = f"lang_{r.lang}"
        (dropped if why else kept).append((r, why) if why else r)
    return kept, dropped


def _candidate_key(rec, priority):
    is_pdf = rec.ext == ".pdf"
    pr = priority.index(rec.source) if rec.source in priority else len(priority)
    return (0 if is_pdf else 1, pr)


def build_plan(records, priority=DEFAULT_PRIORITY):
    """Group by identity, split into versions, order candidates. Returns a list of dicts."""
    groups = defaultdict(list)
    for r in records:
        groups[identity(r)].append(r)

    plan = []
    for key, recs in sorted(groups.items(), key=lambda kv: [str(x) for x in kv[0]]):
        by_src = defaultdict(list)
        for r in recs:
            by_src[r.source].append(r)
        for src in by_src:
            # de-duplicate identical URLs listed twice by the same source
            seen, uniq = set(), []
            for r in sorted(by_src[src], key=lambda x: (x.published_at or "", x.url)):
                if r.url not in seen:
                    seen.add(r.url)
                    uniq.append(r)
            by_src[src] = uniq
        n_src_max = max(len(v) for v in by_src.values())
        if n_src_max == 1:
            # the normal case: one record per source -> ONE document, sources = fallbacks
            versions = [sorted((lst[0] for lst in by_src.values()),
                               key=lambda r: _candidate_key(r, priority))]
        else:
            # some source lists this identity more than once. Pairing records across
            # sources by position or by date both fail on real data (observed: BVH Q3/2024,
            # VHM 6T/2024), so every record becomes its own version, ordered by
            # (restatement marker, posting time). Byte-identical files are collapsed after
            # download by the ingest stage.
            flat = [r for lst in by_src.values() for r in lst]
            flat.sort(key=lambda r: (r.restated, r.published_at or "", r.source))
            versions = [[r] for r in flat]
        n_versions = len(versions)
        for v, cands in enumerate(versions, 1):
            head = cands[0]
            plan.append(dict(
                doc_id=canonical_name(head, v),
                ticker=head.ticker, year=head.year, period_type=head.period_type,
                quarter=head.quarter,
                period=period_str(head.year, head.period_type, head.quarter),
                scope=head.scope, audit=head.audit, doc_kind=head.doc_kind, lang=head.lang,
                version=v, n_versions=n_versions, is_latest=(v == n_versions),
                restated=any(c.restated for c in cands) or v > 1,
                title=head.title, exchange=next((c.exchange for c in cands if c.exchange), ""),
                published_at=head.published_at,
                sources="+".join(sorted({c.source for c in cands})),
                candidates=json.dumps([dict(source=c.source, url=c.url, mirrors=c.mirrors,
                                            ext=c.ext, title=c.title,
                                            published_at=c.published_at)
                                       for c in cands], ensure_ascii=False)))
    return plan
