"""Data classes shared by every stage."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class DocRecord:
    """One document as a SOURCE describes it — before we trust anything.

    Everything here is a claim made by a website. The ingest stage checks the
    claims against the bytes (cover page, unit, text layer) and records where
    each fact finally came from.
    """

    source: str                     # "vietstock" | "cafef" | "ir"
    ticker: str
    title: str                      # the source's own title, verbatim
    url: str
    native_id: str = ""             # the source's id for this record
    published_at: str = ""          # ISO-8601 UTC, when the source says it was posted
    ext: str = ""                   # ".pdf" | ".zip" | ".rar" | ...
    exchange: str = ""              # HOSE | HNX | UPCOM, when the source reveals it
    api_year: Optional[int] = None  # the source's own structured year field
    api_quarter: Optional[int] = None
    mirrors: list = field(default_factory=list)   # alternative URLs for the same bytes

    # --- filled by titles.classify() -------------------------------------
    year: Optional[int] = None
    period_type: str = ""           # Q | H1 | 9M | FY
    quarter: Optional[int] = None   # 1-4 for Q; 0 FY; 6 for H1; 9 for 9M
    scope: str = ""                 # hopnhat | rieng | standalone
    audit: str = ""                 # audited | reviewed | unaudited
    doc_kind: str = ""              # full | partial | explanation
    lang: str = "vi"
    restated: bool = False
    parse_ok: bool = False
    parse_note: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)
