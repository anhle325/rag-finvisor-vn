"""raw/ (whatever the portals served) -> corpus/*.pdf + catalog.csv

Steps per downloaded document:
  1. unpack   .zip / .rar -> the PDFs inside (member names often arrive as
              cp437-mojibake of Vietnamese — repaired here)
  2. dedup    identical bytes (SHA-1) are stored once; the other doc_id is
              recorded as a duplicate, never indexed twice
  3. probe    text layer, text quality, unit, statement form, signature
  4. verify   does the cover say what the source claimed?
  5. catalog  one row per corpus file: the N01 columns first, provenance after
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from .probe import probe_pdf, verify_cover

N01_COLUMNS = ["file", "ticker", "year", "quarter", "period", "scope", "audited", "unit",
               "pages", "chars_per_page", "kind", "size_kb", "sha1", "meta_source"]
EXTRA_COLUMNS = ["doc_id", "part", "n_parts", "period_type", "audit", "scope_raw", "doc_kind",
                 "lang", "version", "n_versions", "is_latest", "restated", "text_quality",
                 "pages_with_text", "signed", "encrypted", "statement_form", "meta_check",
                 "source", "url", "published_at", "title", "archive_member", "exchange",
                 "q_diacritic_ratio", "q_tcvn3_ratio", "q_en_ratio", "cover_letter", "producer", "probe_error",
                 "ocr_file", "ocr_text_quality", "ocr_chars_per_page", "ocr_unit"]


def sha1_file(path, block=1 << 16):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def fix_zip_name(info: zipfile.ZipInfo) -> str:
    """Zip tools on Vietnamese Windows write UTF-8 names without the UTF-8 flag;
    Python then decodes them as cp437 ('BCTC_H╗úp_nhß║Ñt.pdf'). Undo that."""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for enc in ("utf-8", "cp1258", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return name


def _members_zip(path, depth=0):
    out = []
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = fix_zip_name(info)
            data = z.read(info)
            if data[:5] == b"%PDF-":
                out.append((name, data))
            elif data[:4] == b"PK\x03\x04" and depth < 2:
                tmp = io.BytesIO(data)
                out.extend((f"{name}/{n}", d) for n, d in _members_zip(tmp, depth + 1))
    return out


def _pdfs_in_dir(root):
    out = []
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            data = p.read_bytes()
            if data[:5] == b"%PDF-":
                out.append((str(p.relative_to(root)), data))
    return out


def _extract_with_tool(path):
    """bsdtar (built into macOS as `tar`), unar, 7z or unrar — whichever exists."""
    cmds = [["bsdtar", "-xf", "{a}", "-C", "{d}"], ["tar", "-xf", "{a}", "-C", "{d}"],
            ["unar", "-q", "-o", "{d}", "{a}"], ["7z", "x", "-y", "-o{d}", "{a}"],
            ["unrar", "x", "-y", "{a}", "{d}/"]]
    errors = []
    for cmd in cmds:
        if not shutil.which(cmd[0]):
            continue
        with tempfile.TemporaryDirectory() as d:
            argv = [c.replace("{a}", str(path)).replace("{d}", d) for c in cmd]
            r = subprocess.run(argv, capture_output=True, timeout=300)
            found = _pdfs_in_dir(d)
            if found:
                return found
            errors.append(f"{cmd[0]}: rc={r.returncode}")
    raise RuntimeError("cannot extract archive (" + "; ".join(errors or ["no tool"]) +
                       ") — install one of: unar, 7z, unrar  (macOS `tar` usually works)")


def _members_rar(path):
    try:
        import rarfile
        out = []
        with rarfile.RarFile(path) as r:
            for info in r.infolist():
                if info.is_dir():
                    continue
                data = r.read(info)
                if data[:5] == b"%PDF-":
                    out.append((info.filename, data))
        if out:
            return out
    except Exception:                                           # noqa: BLE001
        pass
    return _extract_with_tool(path)


def unpack(raw_path):
    """Returns [(member_name, bytes)] of the PDFs in a downloaded file."""
    ext = Path(raw_path).suffix.lower()
    if ext == ".pdf":
        return [("", Path(raw_path).read_bytes())]
    if ext == ".zip":
        return _members_zip(raw_path)
    if ext == ".rar":
        return _members_rar(raw_path)
    if ext == ".7z":
        return _extract_with_tool(raw_path)
    return []


def _order_parts(members):
    """Archives hold VN + EN versions, or a report split in parts. Keep them all,
    Vietnamese first, then by name so part numbers stay stable across runs."""
    def is_en(n):
        s = n.lower()
        return bool(re.search(r"(^|[^a-z])(en|eng|english|tieng.?anh)([^a-z]|$)", s))
    return sorted(members, key=lambda m: (is_en(m[0]), m[0]))


def ingest(plan_rows, download_log, out_dir, verbose=True):
    out_dir = Path(out_dir)
    corpus = out_dir / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    plan = {r["doc_id"]: r for r in plan_rows}
    seen_sha1 = {}
    rows, events = [], []

    for doc_id, log in download_log.items():
        if log.get("status") != "ok" or doc_id not in plan:
            continue
        p = plan[doc_id]
        raw_path = out_dir / log["raw_path"]
        try:
            members = _order_parts(unpack(raw_path))
        except Exception as e:                                  # noqa: BLE001
            events.append(dict(doc_id=doc_id, event="unpack_failed", detail=str(e)[:200]))
            continue
        if not members:
            events.append(dict(doc_id=doc_id, event="no_pdf_inside", detail=log["raw_path"]))
            continue

        for k, (member, data) in enumerate(members, 1):
            fname = doc_id + (f"__p{k}" if len(members) > 1 else "") + ".pdf"
            h = hashlib.sha1(data).hexdigest()
            if h in seen_sha1:
                events.append(dict(doc_id=doc_id, event="duplicate_bytes",
                                   detail=f"{fname} == {seen_sha1[h]}"))
                continue
            dest = corpus / fname
            if not dest.exists() or sha1_file(dest) != h:
                dest.write_bytes(data)
            seen_sha1[h] = fname

            pr = probe_pdf(str(dest))
            # only text we trust can contradict the source: an OCR layer without
            # diacritics ("Hop NrrAr") would produce false 'scope' mismatches
            check = (verify_cover(pr["cover_text"], p["ticker"], p["year"],
                                  "rieng" if p["scope"] == "standalone" else p["scope"])
                     if pr["text_quality"] == "ok" else "unverifiable")
            if check.startswith("mismatch"):
                events.append(dict(doc_id=doc_id, event="cover_" + check, detail=fname))
            q = int(p["quarter"]) if str(p["quarter"]).lstrip("-").isdigit() else p["quarter"]
            rows.append(dict(
                file=fname, ticker=p["ticker"], year=int(p["year"]), quarter=q,
                period=p["period"],
                scope="rieng" if p["scope"] == "standalone" else p["scope"],
                audited=p["audit"] == "audited", unit=pr["unit"], pages=pr["pages"],
                chars_per_page=pr["chars_per_page"], kind=pr["kind"],
                size_kb=round(len(data) / 1024, 1), sha1=h,
                meta_source="source+cover" if check == "ok" else "source",
                doc_id=doc_id, part=k, n_parts=len(members), period_type=p["period_type"],
                audit=p["audit"], scope_raw=p["scope"], doc_kind=p["doc_kind"],
                lang="en" if pr["text_quality"] == "english" else p["lang"],
                version=p["version"], n_versions=p["n_versions"], is_latest=p["is_latest"],
                restated=p["restated"], text_quality=pr["text_quality"],
                pages_with_text=pr["pages_with_text"], signed=pr["signed"],
                encrypted=pr["encrypted"], statement_form=pr["statement_form"],
                meta_check=check, source=log["source"], url=log["url"],
                published_at=p["published_at"], title=p["title"], archive_member=member,
                exchange=p.get("exchange", ""),
                q_diacritic_ratio=pr.get("q_diacritic_ratio"),
                q_tcvn3_ratio=pr.get("q_tcvn3_ratio"), q_en_ratio=pr.get("q_en_ratio"),
                cover_letter=pr["cover_letter"], producer=pr["producer"],
                probe_error=pr.get("error", "")))
            if verbose:
                print(f"  {fname:44s} {pr['pages']:3d}p {pr['kind']:8s} "
                      f"{pr['text_quality']:13s} unit={pr['unit']} {check}")

    rows.sort(key=lambda r: (r["ticker"], r["year"], str(r["quarter"]), r["scope"], r["file"]))
    ocr_log = out_dir / "ocr_log.csv"          # keep OCR results across re-ingests
    if ocr_log.exists():
        with open(ocr_log, encoding="utf-8") as fh:
            ocr = {r["file"]: r for r in csv.DictReader(fh) if r["status"] == "ok"}
        for r in rows:
            o = ocr.get(r["file"])
            if o and (out_dir / o["ocr_file"]).exists():
                r.update(ocr_file=o["ocr_file"], ocr_text_quality=o["ocr_text_quality"],
                         ocr_chars_per_page=o["ocr_chars_per_page"],
                         ocr_unit=o.get("ocr_unit", ""))
    _write_csv(out_dir / "catalog.csv", rows, N01_COLUMNS + EXTRA_COLUMNS)
    _write_csv(out_dir / "ingest_events.csv", events, ["doc_id", "event", "detail"])
    _write_companies(out_dir, rows)
    return rows, events


def _write_companies(out_dir, rows):
    by = defaultdict(list)
    for r in rows:
        by[r["ticker"]].append(r)
    comp = []
    for t, rs in sorted(by.items()):
        units = Counter(r["unit"] for r in rs if r["unit"])
        forms = Counter(r["statement_form"] for r in rs if r["statement_form"])
        comp.append(dict(ticker=t, exchange=next((r["exchange"] for r in rs if r["exchange"]), ""),
                         reporting_unit=units.most_common(1)[0][0] if units else "",
                         statement_form=forms.most_common(1)[0][0] if forms else "",
                         has_subsidiaries=int(any(r["scope_raw"] == "hopnhat" for r in rs)),
                         n_files=len(rs)))
    _write_csv(Path(out_dir) / "companies.csv", comp,
               ["ticker", "exchange", "reporting_unit", "statement_form", "has_subsidiaries",
                "n_files"])


def _write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
