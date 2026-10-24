"""Optional stage: give scanned / badly-OCR'd PDFs a usable Vietnamese text layer.

    # spike O1: 6 varied documents, first 15 pages -> speed + failure modes
    python -m bctc_collector ocr --out pack_real --sample-docs 6 --ocr-pages 1-15
    # spike O2: 12 varied documents, whole files -> accuracy against the reference
    python -m bctc_collector ocr --out pack_real --sample-docs 12
    # full run (resumes; files OCR'd only partially in a spike are redone)
    python -m bctc_collector ocr --out pack_real --jobs 4

Uses OCRmyPDF + Tesseract with the Vietnamese model:

    macOS : brew install ocrmypdf tesseract-lang
    Linux : apt install ocrmypdf tesseract-ocr-vie

On the real corpus this is not an edge case: most filings are scans of signed paper
(often with an OCR layer the scanner added WITHOUT Vietnamese diacritics). Mode per
document:

    kind scanned / mixed              -> --skip-text   (OCR only pages without text)
    text_quality no_diacritics         -> --redo-ocr    (replace the scanner's OCR layer)
    text_quality cid_garbage / legacy  -> --force-ocr   (rasterise, then OCR everything)

Output goes to corpus_ocr/<same file name>; the original is never modified.
`ocr_log.csv` records mode, seconds and the re-probed text quality, and
catalog.csv gets the columns ocr_file / ocr_text_quality / ocr_chars_per_page.
"""

from __future__ import annotations

import csv
import re
import shutil
import statistics
import subprocess
import time
from collections import Counter
from pathlib import Path

from .probe import probe_pdf
from .titles import strip_accents

OCR_FIELDS = ["file", "mode", "status", "seconds", "pages", "ocr_pages", "sec_per_page",
              "ocr_chars_per_page", "ocr_text_quality", "ocr_unit", "ta_pdf", "ta_ref",
              "ta_check", "ta_page", "bs_identity", "ocr_file", "detail"]


def needs_ocr(row):
    if row.get("kind") in ("scanned", "mixed"):
        return "skip-text"
    q = row.get("text_quality")
    if q == "no_diacritics":
        return "redo-ocr"
    if q in ("cid_garbage", "legacy_font"):
        return "force-ocr"
    return None


def check_tools(lang="vie"):
    if not shutil.which("ocrmypdf"):
        raise RuntimeError("ocrmypdf not found — macOS: brew install ocrmypdf tesseract-lang · "
                           "Linux: apt install ocrmypdf tesseract-ocr-vie")
    langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True)
    have = set((langs.stdout + langs.stderr).split())
    missing = [l for l in lang.split("+") if l not in have]
    if missing:
        raise RuntimeError(f"tesseract language(s) missing: {missing} — macOS: brew install "
                           "tesseract-lang · Linux: apt install tesseract-ocr-vie")


_HELP = None


def _supports(flag):
    global _HELP
    if _HELP is None:
        _HELP = subprocess.run(["ocrmypdf", "--help"], capture_output=True, text=True).stdout
    return flag in _HELP


def _decrypted_copy(src, tmpdir):
    """OCRmyPDF refuses encrypted input. Most 'encrypted' filings only carry an owner
    password (no printing / no copying); pypdf can remove it (needs `cryptography`)."""
    from pypdf import PdfReader, PdfWriter
    rd = PdfReader(str(src))
    if rd.is_encrypted:
        rd.decrypt("")
    w = PdfWriter(clone_from=rd)
    dst = Path(tmpdir) / Path(src).name
    with open(dst, "wb") as fh:
        w.write(fh)
    return dst


def _run(mode, lang, jobs, r, src, dst, timeout, pages=None, env=None):
    if str(r.get("encrypted")) == "True":
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            try:
                plain = _decrypted_copy(src, td)
            except Exception as e:                              # noqa: BLE001
                return False, f"decrypt failed: {type(e).__name__}: {str(e)[:120]}"
            return _run(mode, lang, jobs, dict(r, encrypted="False"), plain, dst, timeout,
                        pages, env)
    argv = ["ocrmypdf", f"--{mode}", "-l", lang, "--jobs", str(jobs), "--optimize", "0",
            "--output-type", "pdf", "--quiet"]
    if pages:
        argv += ["--pages", pages]
    if str(r.get("signed")) == "True" and _supports("--invalidate-digital-signatures"):
        # most filings are e-signed and OCRmyPDF refuses to touch them otherwise. The
        # ORIGINAL keeps its valid signature — only the corpus_ocr/ copy loses it.
        argv.append("--invalidate-digital-signatures")
    argv += [str(src), str(dst)]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    ok = p.returncode == 0 and Path(dst).exists()
    msg = (p.stderr or p.stdout or "").strip().splitlines()
    return ok, "" if ok else (msg[-1][:200] if msg else f"rc={p.returncode}")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(line.replace("\0", "") for line in fh))


def _n_pages(spec, total):
    if not spec:
        return total
    n = 0
    for part in spec.split(","):
        a, _, b = part.partition("-")
        a = int(a)
        b = int(b) if b else a
        n += max(0, min(b, total) - a + 1)
    return n


def pick_varied(rows, n):
    """Round-robin over (kind/text layer, producer family, ticker) so a spike of n
    documents sees as many DIFFERENT situations as possible."""
    groups = {}
    for r in sorted(rows, key=lambda r: r["file"]):
        key = (needs_ocr(r), (r.get("producer") or "?").split(" ")[0][:12])
        groups.setdefault(key, []).append(r)
    out, seen_t = [], set()
    while len(out) < n and any(groups.values()):
        for key in sorted(groups):
            g = groups[key]
            if not g:
                continue
            # prefer a ticker we have not seen yet
            i = next((j for j, r in enumerate(g) if r["ticker"] not in seen_t), 0)
            r = g.pop(i)
            out.append(r)
            seen_t.add(r["ticker"])
            if len(out) >= n:
                break
    return out


# ---------------------------------------------------------------- accuracy check
_NUM = re.compile(r"\(?\d{1,3}(?:[.,]\d{3}){2,}\)?")
_MULT = {"dong": 1, "nghin": 1_000, "trieu": 1_000_000, "ty": 1_000_000_000}


RE_TA_LABEL = re.compile(r"\btong\s*(cong\s*)?tai\s*sa{1,2}n\b")   # OCR: "TÀI SẲẢN" -> "tai saan"   # after strip_accents: OCR's
                                                              # "TÔNG CỘNG TẢI SẢN" matches too


_PAGE_CACHE = {}


def _page_texts(pdf, max_pages):
    key = (str(pdf), max_pages, Path(pdf).stat().st_mtime)
    if key not in _PAGE_CACHE:
        _PAGE_CACHE.clear()                 # one document at a time is enough
        _PAGE_CACHE[key] = _page_texts_uncached(pdf, max_pages)
    return _PAGE_CACHE[key]


def _page_texts_uncached(pdf, max_pages):
    if shutil.which("pdftotext"):
        out = []
        for i in range(1, max_pages + 1):
            t = subprocess.run(["pdftotext", "-layout", "-f", str(i), "-l", str(i), str(pdf), "-"],
                               capture_output=True, text=True, timeout=60)
            if t.returncode != 0:
                break
            out.append(t.stdout)
        return out
    from pypdf import PdfReader
    return [(p.extract_text() or "") for p in PdfReader(pdf).pages[:max_pages]]


RE_TNV_LABEL = re.compile(r"\btong\s*(cong\s*)?nguon\s*von\b"
                          r"|\btong\s*(cong\s*)?no\s*phai\s*tra\s*va\s*von\s*chu\s*so\s*huu\b")  # banks


def labeled_value(txt, label=None):
    """(first statement-size number on the labelled line, label_seen) for ONE page of text.
    Works on any engine's output, markdown tables included."""
    label = label or RE_TA_LABEL
    lines = txt.splitlines()
    seen = False
    for i, ln in enumerate(lines):
        m = label.search(strip_accents(ln.lower()))
        if not m:
            continue
        seen = True
        # a long label wraps: "TỔNG CỘNG NGUỒN VỐN (440 = 300 +" / "400)   95.758..."
        # (only continuation lines with almost no words, blank lines skipped —
        #  never the next row of the table)
        cont = []
        for x in lines[i + 1:i + 5]:
            if not x.strip():
                continue
            if len(re.sub(r"[^a-zA-Z]", "", strip_accents(x))) >= 12:
                break
            cont.append(x)
            if _NUM.search(x):
                break
        tail = " ".join([ln[m.end():]] + cont)
        # "(270 = 100 + 200)" under TT200, "(280 = 100 + 200)" under TT99/2025
        tail = re.sub(r"\(\s*\d{3}\s*=[^)]*\)", " ", tail)
        nums = _NUM.findall(tail)
        if nums:
            v = int(re.sub(r"[^\d]", "", nums[0]))
            return (-v if nums[0].startswith("(") else v), True
    return None, seen


def find_total_assets(pdf, max_pages=60, label=None):
    """Locate the 'Tổng cộng tài sản' line and read the first big number on it.

    Returns (value or None, 1-based page or None, status). Status is 'found',
    'label_no_number' (the line is there but OCR broke the digits — observed:
    HPG Q1/2026 '270 gen5228') or 'label_not_found'. The label, not the code 270, is
    the anchor: banks have no code column ('TỔNG TÀI SẢN CÓ') and some groups print
    the code in the left margin, where '270' belongs to another line (GVR Q1/2026)."""
    try:
        pages = _page_texts(pdf, max_pages)
    except Exception:                                           # noqa: BLE001
        return None, None, "unreadable"
    label_seen = None
    for pno, txt in enumerate(pages, 1):
        if label_seen and pno > label_seen + 1:
            # the statement page had the label but OCR broke its number: do NOT go on to
            # the notes, where "tổng tài sản" of a segment or a subsidiary lives
            # (observed: HPG Q1/2026 picked 5.472.467 from page 35)
            break
        v, seen = labeled_value(txt, label or RE_TA_LABEL)
        if seen:
            label_seen = label_seen or pno
        if v is not None:
            return v, pno, "found"
    return None, label_seen, "label_no_number" if label_seen else "label_not_found"


def read_total_assets(pdf, max_pages=60):
    return find_total_assets(pdf, max_pages)[0]


def load_reference(out):
    ref = {}
    for r in _read(out / "reference_financials.csv") if (out / "reference_financials.csv").exists() else []:
        if r.get("total_assets"):
            ref[(r["ticker"], str(r["year"]), str(r["quarter"]))] = (int(float(r["total_assets"])),
                                                                     r.get("scope", ""))
    return ref


def check_total_assets(row, pdf, unit, ref):
    """'match' · 'unit_wrong_x1000'… (the number is right, the unit we detected is not)
    · 'mismatch' · 'label_no_number' · 'label_not_found' · 'no_reference' · 'other_scope'."""
    check_total_assets.last_page = None
    if row.get("period_type") not in ("Q", "FY"):
        return None, None, "no_reference"
    key = (row["ticker"], str(row["year"]), str(0 if row["period_type"] == "FY" else row["quarter"]))
    if key not in ref:
        return None, None, "no_reference"
    ta_ref, ref_scope = ref[key]
    want = "hopnhat" if ref_scope == "hopnhat" else "standalone"
    if row.get("scope_raw", row.get("scope")) != want:
        return None, ta_ref, "other_scope"
    v, page, status = find_total_assets(pdf)
    check_total_assets.last_page = page
    if v is None:
        return None, ta_ref, status
    for mult in [_MULT.get(unit or "dong", 1)] + [m for m in (1, 1_000, 1_000_000)
                                                   if m != _MULT.get(unit or "dong", 1)]:
        if abs(v * mult - ta_ref) <= 0.0005 * ta_ref:
            name = "match" if mult == _MULT.get(unit or "dong", 1) else f"unit_wrong_x{mult}"
            return v * mult, ta_ref, name
    return v * _MULT.get(unit or "dong", 1), ta_ref, "mismatch"


def evaluate(r, dst, ref):
    """All the checks that need no OCR run: probe + total assets vs reference + identity."""
    pr = probe_pdf(str(dst))
    unit = pr["unit"] or r.get("unit") or ""
    ta_pdf, ta_ref, chk = check_total_assets(r, dst, unit, ref)
    ta_page = getattr(check_total_assets, "last_page", None)
    # reference-free accuracy signal: total assets must equal total liabilities +
    # equity. One misread digit in either breaks it.
    a, _, _ = find_total_assets(dst)
    b, _, _ = find_total_assets(dst, label=RE_TNV_LABEL)
    identity = "ok" if a and b and a == b else "broken" if a and b else "not_found"
    return dict(ocr_chars_per_page=pr["chars_per_page"], ocr_text_quality=pr["text_quality"],
                ocr_unit=pr["unit"] or "", ta_pdf=ta_pdf or "", ta_ref=ta_ref or "",
                ta_check=chk, ta_page=ta_page or "", bs_identity=identity)


def recheck(out_dir):
    """Re-run the checks on files already OCR'd (after a fix to the checks)."""
    out = Path(out_dir)
    cat = {r["file"]: r for r in _read(out / "catalog.csv")}
    ref = load_reference(out)
    done = {r["file"]: r for r in _read(out / "ocr_log.csv")}
    for f, rec in done.items():
        if rec.get("status") == "ok" and f in cat and (out / rec["ocr_file"]).exists():
            rec.update(evaluate(cat[f], out / rec["ocr_file"], ref))
            print(f"  {f:40s} TA:{rec['ta_check']:16s} id:{rec['bs_identity']}")
    _write(out / "ocr_log.csv", done.values(), OCR_FIELDS)
    write_ocr_report(out, done)
    return done


BEST_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/{lang}.traineddata"


def prepare_tessdata_best(lang="vie", cache=None):
    """A private tessdata folder = the system one (configs included — OCRmyPDF needs
    them) + the 'best' LSTM model for `lang` (vie: ~12 MB vs ~0.5 MB for 'fast')."""
    import urllib.request
    cache = Path(cache or Path.home() / ".cache" / "bctc_collector" / "tessdata_best")
    probe = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True)
    m = re.search(r'"([^"]+)"', probe.stdout + probe.stderr)
    if not m:
        raise RuntimeError("cannot find the system tessdata folder (tesseract --list-langs)")
    system = Path(m.group(1))
    cache.mkdir(parents=True, exist_ok=True)
    for item in system.iterdir():
        dst = cache / item.name
        if dst.exists():
            continue
        if item.is_dir():
            shutil.copytree(item, dst)
        elif not item.name.startswith(tuple(lang.split("+"))):
            shutil.copy2(item, dst)
    for lg in lang.split("+"):
        target = cache / f"{lg}.traineddata"
        if not target.exists() or target.stat().st_size < 1_000_000:
            print(f"downloading tessdata_best/{lg}.traineddata ...")
            urllib.request.urlretrieve(BEST_URL.format(lang=lg), target)
    return cache


def ocr_corpus(out_dir, lang="vie", jobs=4, max_docs=None, only_latest=True, verbose=True,
               timeout=3600, pages=None, sample_docs=None, variant=None, files=None,
               tessdata=None):
    """variant='best' + tessdata=<dir> runs an A/B experiment into corpus_ocr_best/,
    ocr_log_best.csv, ocr_report_best.md — the main OCR output and the catalog are
    left untouched. files='broken' picks the files whose identity check failed."""
    import os
    out = Path(out_dir)
    if tessdata == "best":
        tessdata = str(prepare_tessdata_best(lang))
    env = dict(os.environ, TESSDATA_PREFIX=str(Path(tessdata).expanduser())) if tessdata else None
    if env:
        langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True,
                               env=env)
        if lang.split("+")[0] not in (langs.stdout + langs.stderr).split():
            raise RuntimeError(f"'{lang}' not found in {tessdata} (need {lang}.traineddata)")
    else:
        check_tools(lang)
    cat_path = out / "catalog.csv"
    cat = _read(cat_path)
    sub = f"corpus_ocr_{variant}" if variant else "corpus_ocr"
    log_path = out / (f"ocr_log_{variant}.csv" if variant else "ocr_log.csv")
    if files == "broken":
        main = _read(out / "ocr_log.csv") if (out / "ocr_log.csv").exists() else []
        files = [r["file"] for r in main if r.get("bs_identity") in ("broken", "not_found")
                 or r.get("ta_check") in ("mismatch", "label_no_number", "label_not_found")]
    elif isinstance(files, str):
        files = [f.strip() for f in files.split(",") if f.strip()]
    done = {r["file"]: r for r in _read(log_path)} if log_path.exists() else {}
    ref = load_reference(out)
    todo = [r for r in cat if needs_ocr(r) and r.get("lang", "vi") == "vi"
            and (not only_latest or r.get("is_latest") in ("True", "1", "true"))]
    # resume: skip what is done — but a file OCR'd on a page subset is not done
    # for a full run
    todo = [r for r in todo if not (done.get(r["file"], {}).get("status") == "ok"
                                    and (pages or done[r["file"]].get("ocr_pages", "all") in ("", "all")))]
    if files:
        todo = [r for r in todo if r["file"] in set(files)]
    if sample_docs:
        todo = pick_varied(todo, sample_docs)
    if max_docs:
        todo = todo[:max_docs]
    n_pages = sum(_n_pages(pages, int(r["pages"] or 0)) for r in todo)
    if verbose:
        print(f"ocr: {len(todo)} documents, {n_pages} pages to OCR "
              f"(rough estimate {n_pages * 2.2 / max(1, jobs) / 60:.0f} min with {jobs} jobs)")
    (out / sub).mkdir(exist_ok=True)
    for i, r in enumerate(todo, 1):
        mode = needs_ocr(r)
        src, dst = out / "corpus" / r["file"], out / sub / r["file"]
        t0 = time.time()
        ok, detail = _run(mode, lang, jobs, r, src, dst, timeout, pages, env)
        if not ok and mode == "redo-ocr":
            # --redo-ocr refuses PDFs with form fields — and an e-signature IS a form
            # field (observed: REE, OmniPage OCR layer + signature). Rasterise instead.
            mode = "force-ocr"
            ok, detail = _run(mode, lang, jobs, r, src, dst, timeout, pages, env)
        secs = time.time() - t0
        k = _n_pages(pages, int(r["pages"] or 0))
        rec = dict(file=r["file"], mode=mode, status="ok" if ok else "failed",
                   seconds=round(secs, 1), pages=r["pages"], ocr_pages=pages or "all",
                   sec_per_page=round(secs / max(1, k), 2), detail=detail)
        if ok:
            rec.update(evaluate(r, dst, ref), ocr_file=f"{sub}/{r['file']}")
        done[r["file"]] = rec
        if verbose:
            print(f"[{i}/{len(todo)}] {r['file']:40s} {mode:10s} {rec['status']:6s} "
                  f"{rec['sec_per_page']:5.2f}s/p  {rec.get('ocr_text_quality', detail):13s} "
                  f"unit={rec.get('ocr_unit', '')} TA:{rec.get('ta_check', '')} "
                  f"id:{rec.get('bs_identity', '')}")
        _write(log_path, done.values(), OCR_FIELDS)

    if variant:                                   # an experiment: report only
        write_ocr_report(out, done, name=f"ocr_report_{variant}.md")
        return done
    # merge into the catalog
    extra = ["ocr_file", "ocr_text_quality", "ocr_chars_per_page", "ocr_unit", "ocr_pages"]
    fields = list(cat[0].keys()) if cat else []
    fields += [f for f in extra if f not in fields]
    for r in cat:
        d = done.get(r["file"])
        for f in extra:
            r[f] = d.get(f, "") if d and d.get("status") == "ok" else r.get(f, "")
    _write(cat_path, cat, fields)
    write_ocr_report(out, done)
    return done


def write_ocr_report(out, done, name="ocr_report.md"):
    rows = list(done.values())
    okr = [r for r in rows if r["status"] == "ok"]
    spp = [float(r["sec_per_page"]) for r in okr if r.get("sec_per_page")]
    L = [f"# OCR report — `{Path(out).name}`", "",
         f"- documents: {len(rows)} · ok {len(okr)} · failed {len(rows) - len(okr)}",
         f"- modes: {dict(Counter(r['mode'] for r in rows))}",
         f"- seconds per page (wall): median {statistics.median(spp):.2f}, "
         f"max {max(spp):.2f}" if spp else "- seconds per page: n/a",
         f"- text quality after OCR: {dict(Counter(r.get('ocr_text_quality') for r in okr))}",
         f"- unit found after OCR: {dict(Counter(r.get('ocr_unit') or 'NOT FOUND' for r in okr))}",
         f"- total assets vs reference: {dict(Counter(r.get('ta_check') for r in okr))}",
         f"- balance identity on the page (tổng tài sản = tổng nguồn vốn): "
         f"{dict(Counter(r.get('bs_identity') for r in okr))}",
         f"- page where 'Tổng cộng tài sản' was found: "
         f"{sorted(int(r['ta_page']) for r in okr if str(r.get('ta_page') or '').isdigit())}",
         "", "| file | mode | pages | s/page | quality | unit | total assets | page | detail |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['file']} | {r['mode']} | {r.get('ocr_pages')} | {r.get('sec_per_page')} | "
                 f"{r.get('ocr_text_quality', '')} | {r.get('ocr_unit', '')} | "
                 f"{r.get('ta_check', '')} | {r.get('ta_page', '')} | {(r.get('detail') or '')[:60]} |")
    (Path(out) / name).write_text("\n".join(L) + "\n", encoding="utf-8")


def _write(path, rows, fields):
    tmp = Path(path).with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)
