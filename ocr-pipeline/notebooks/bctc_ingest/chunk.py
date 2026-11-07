"""Turn per-page text into retrieval chunks.

A deliberately simple, robust v1: pack paragraphs into chunks of about `chunk_size`
characters, with `overlap` characters of context carried from the end of one chunk into the
next, and never split a paragraph across chunks unless the paragraph alone is larger than the
target. Each chunk records the page numbers it spans, so an answer can cite "VNM 2025, p.12".

The chunker is intentionally isolated in one function so it can be swapped later (semantic or
table-aware chunking) without touching the rest of the pipeline.
"""

from __future__ import annotations

import re

_PARA_SPLIT = re.compile(r"\n\s*\n")        # blank line separates paragraphs
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")   # fallback split for an oversized paragraph


def _blocks(pages):
    """Flatten [(page_no, text)] into [(page_no, paragraph)] blocks."""
    out = []
    for page_no, text in pages:
        for para in _PARA_SPLIT.split(text or ""):
            para = para.strip()
            if para:
                out.append((page_no, para))
    return out


def _split_oversized(page_no, para, chunk_size):
    """Break one huge paragraph into <=chunk_size pieces on sentence boundaries."""
    pieces, cur = [], ""
    for sent in _SENT_SPLIT.split(para):
        if cur and len(cur) + 1 + len(sent) > chunk_size:
            pieces.append((page_no, cur.strip()))
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur.strip():
        pieces.append((page_no, cur.strip()))
    return pieces


def chunk_pages(pages, chunk_size: int = 1200, overlap: int = 150) -> list:
    """Chunk [(page_no, text)] into [{text, pages, n_chars}].

    pages     : list of (1-based page number, cleaned page text)
    chunk_size: target characters per chunk
    overlap   : characters of trailing context repeated at the start of the next chunk
    """
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    # normalise to blocks no larger than chunk_size
    blocks = []
    for page_no, para in _blocks(pages):
        if len(para) <= chunk_size:
            blocks.append((page_no, para))
        else:
            blocks.extend(_split_oversized(page_no, para, chunk_size))

    chunks, buf, buf_pages = [], "", set()

    def flush():
        if buf.strip():
            chunks.append({"text": buf.strip(),
                           "pages": sorted(buf_pages),
                           "n_chars": len(buf.strip())})

    for page_no, para in blocks:
        if buf and len(buf) + 2 + len(para) > chunk_size:
            flush()
            # start the next chunk with the overlap tail of the previous one
            tail = buf[-overlap:] if overlap else ""
            # don't cut mid-word: keep from the first space in the tail
            if tail and " " in tail:
                tail = tail[tail.index(" ") + 1:]
            buf = (tail + "\n\n" + para) if tail else para
            buf_pages = set([page_no])
        else:
            buf = (buf + "\n\n" + para) if buf else para
            buf_pages.add(page_no)
    flush()

    return chunks
