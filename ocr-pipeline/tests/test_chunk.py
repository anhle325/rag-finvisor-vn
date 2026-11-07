"""The chunker: sizing, overlap, page spans, and oversized-paragraph splitting."""

from bctc_ingest.chunk import chunk_pages


def test_small_input_is_one_chunk():
    pages = [(1, "Mot doan van ngan."), (2, "Doan van thu hai.")]
    chunks = chunk_pages(pages, chunk_size=1000, overlap=100)
    assert len(chunks) == 1
    assert chunks[0]["pages"] == [1, 2]


def test_respects_chunk_size_and_records_pages():
    para = "cau van. " * 40                       # ~360 chars per paragraph
    pages = [(1, para + "\n\n" + para), (2, para)]
    chunks = chunk_pages(pages, chunk_size=400, overlap=50)
    assert len(chunks) >= 3
    # no chunk is wildly over the target (allow the overlap tail)
    assert all(c["n_chars"] <= 400 + 60 for c in chunks)
    # pages are recorded and are 1-based page numbers from the input
    assert all(c["pages"] for c in chunks)
    assert set().union(*(set(c["pages"]) for c in chunks)) <= {1, 2}


def test_overlap_carries_context():
    pages = [(1, "AAipsum. " * 30 + "\n\n" + "BBdolor. " * 30)]
    chunks = chunk_pages(pages, chunk_size=300, overlap=80)
    assert len(chunks) >= 2
    # the tail of chunk 0 should reappear at the start of chunk 1
    tail = chunks[0]["text"][-40:]
    assert any(word in chunks[1]["text"] for word in tail.split()[:3])


def test_oversized_paragraph_is_split_on_sentences():
    big = "Day la mot cau. " * 100                 # one ~1600-char paragraph, no blank lines
    chunks = chunk_pages([(1, big)], chunk_size=300, overlap=0)
    assert len(chunks) >= 5
    assert all(c["n_chars"] <= 320 for c in chunks)


def test_overlap_must_be_smaller_than_chunk_size():
    import pytest
    with pytest.raises(ValueError):
        chunk_pages([(1, "x")], chunk_size=100, overlap=100)
