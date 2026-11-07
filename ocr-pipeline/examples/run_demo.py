"""Standalone demo of the ingestion pipeline — the script form of notebooks/ocr_ingestion_lab.ipynb.

Runs fully offline: it generates a small sample PDF, runs the text path end to end, writes the
JSON output, and prints a summary. No corpus and no network needed.

    python examples/run_demo.py

To run on the real corpus instead, use the CLI:
    python -m bctc_ingest run --corpus ../data-pipeline/pack_real/corpus \\
                              --catalog ../data-pipeline/pack_real/catalog.csv --out ingest_out
"""

import json
import tempfile
from pathlib import Path

import pymupdf

from bctc_ingest import ocr_paddle
from bctc_ingest.pipeline import IngestConfig, export_jsonl, ingest_document


def make_sample_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    body = ("Bao cao tai chinh hop nhat nam 2025. Don vi tinh trieu dong. "
            "Tong cong tai san va nguon von duoc trinh bay day du theo Thong tu 200. ") * 8
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"Trang {i + 1}\n\n{body}\n\nMa so 270 Tong cong tai san.",
                         fontsize=11)
    doc.save(path)
    doc.close()
    return path


def main():
    print("PaddleOCR available:", ocr_paddle.available(),
          "(False is fine — the text path still works)")
    work = Path(tempfile.mkdtemp()) # cần đặt vào workspacce folder path
    pdf = make_sample_pdf(work / "VNM_BCTC_HN_N2025.pdf") # đổi thành file path pdf bctc thực tế

    meta = dict(ticker="VNM", year=2025, quarter=0, period="2025", scope="hopnhat",
                audited=True, unit="trieu", exchange="HOSE")
    cfg = IngestConfig(chunk_size=500, overlap=80)
    res = ingest_document(pdf, work / "out", cfg, meta=meta, ocr_engine=None, force="text")
    print("result:", res)

    doc_json = json.loads((work / "out" / res["json_path"]).read_text(encoding="utf-8"))
    print(f"\n{doc_json['n_chunks']} chunks written to {res['json_path']}")
    print("first chunk:\n", json.dumps(doc_json["chunks"][0], ensure_ascii=False, indent=2)[:500])

    export_jsonl(work / "out")
    print("\noutput tree:")
    for p in sorted((work / "out").rglob("*")):
        print("  ", p.relative_to(work / "out"))


if __name__ == "__main__":
    main()
