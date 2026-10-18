"""Build a small OCR benchmark ("bake-off") set from packs already collected.

    python -m bctc_collector bench --inputs pack_top50 --out ocr_bench

Output (ship ocr_bench.zip to Colab / Drive):

    ocr_bench/images/<page_id>.png     200 dpi grayscale page images
    ocr_bench/gt/<page_id>.txt          exact text  (digital twins only)
    ocr_bench/meta.csv                  one row per page: set, source, page, form, reference

Two sets, see ocr_eval.py for why:
  twin  pages of a born-digital filing, rendered and degraded like a scan (JPEG,
        slight blur and skew); ground truth = the PDF's own text layer
  scan  the balance-sheet pages ('Tổng cộng tài sản' / 'Tổng cộng nguồn vốn') of real
        scanned filings, chosen to cover corp / bank / insurance, TT200 / TT99, 2020–2026
"""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .ocr import RE_TNV_LABEL, find_total_assets, load_reference

META = ["page_id", "set", "pack", "file", "page", "ticker", "year", "period_type", "quarter",
        "scope_raw", "form", "accounting", "unit", "ta_ref", "role"]

BANKS = {"ACB", "BID", "CTG", "EIB", "HDB", "LPB", "MBB", "MSB", "SHB", "SSB", "STB", "TCB",
         "TPB", "VCB", "VIB", "VPB"}
INSURERS = {"BVH"}
SECURITIES = {"SSI", "VCI", "VND", "HCM", "TCX", "SHS"}


def _read(p):
    with open(p, encoding="utf-8") as fh:
        return list(csv.DictReader(line.replace("\0", "") for line in fh))


def form_of(ticker):
    return ("bank" if ticker in BANKS else "insurance" if ticker in INSURERS
            else "securities" if ticker in SECURITIES else "corp")


def render(pdf, page, dst, dpi=200):
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(page), "-l",
                        str(page), str(pdf), f"{td}/p"], check=True, capture_output=True)
        png = next(Path(td).glob("p*.png"))
        shutil.copy(png, dst)


def degrade(path, seed=0):
    """Make a clean render look like a office scan: skew, blur, JPEG, speckle."""
    import random

    from PIL import Image, ImageFilter
    rnd = random.Random(seed)
    im = Image.open(path).convert("L")
    im = im.rotate(rnd.uniform(-0.6, 0.6), expand=False, fillcolor=255, resample=Image.BICUBIC)
    im = im.filter(ImageFilter.GaussianBlur(radius=0.7))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=40)
    im = Image.open(io.BytesIO(buf.getvalue())).convert("L")
    px = im.load()
    w, h = im.size
    for _ in range(w * h // 1500):                              # dust
        px[rnd.randrange(w), rnd.randrange(h)] = rnd.choice((0, 90, 255))
    im.save(path)


def page_text(pdf, page):
    return subprocess.run(["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(pdf), "-"],
                          capture_output=True, text=True).stdout


def page_kind(text, page_no):
    """Title first (the header names the statement), codes second — a cash-flow page
    full of numbers like 1.270.440 must not pass for a balance sheet."""
    head = text.lower()[:700]
    if "lưu chuyển tiền" in head:
        return "cashflow"
    if "kết quả hoạt động kinh doanh" in head or "kết quả kinh doanh" in head:
        return "income"
    if "cân đối kế toán" in head or "tình hình tài chính" in head:
        return "balance"
    if "thuyết minh" in head:
        return "notes"
    from .ocr_eval import balance_rows
    codes = set(balance_rows(text))
    if len(codes & {"100", "110", "200", "270", "280", "300", "400", "440"}) >= 2:
        return "balance"
    return "notes" if page_no > 10 else "narrative"


def build_bench(inputs, out_dir, twin_docs=None, scan_docs=None, max_twin_pages=8):
    out = Path(out_dir)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "gt").mkdir(exist_ok=True)
    rows = []
    for pack in map(Path, inputs):
        cat = {r["file"]: r for r in _read(pack / "catalog.csv")}
        ref = load_reference(pack)

        # ---------------- digital twins: statement + notes + narrative pages
        twins = twin_docs or [f for f, r in cat.items()
                              if r["kind"] == "digital" and r["text_quality"] == "ok"
                              and r["lang"] == "vi"]
        for f in twins:
            if f not in cat:
                continue
            r, pdf = cat[f], pack / "corpus" / f
            n = int(r["pages"])
            picked, kinds = [], set()
            for p in range(1, n + 1):
                t = page_text(pdf, p)
                if len(t.strip()) < 800:
                    continue
                kind = page_kind(t, p)
                if kind in ("notes",) and sum(k == "notes" for _, k in picked) >= 2:
                    continue
                if kind != "notes" and kind in kinds and kind != "balance":
                    continue
                kinds.add(kind)
                picked.append((p, kind))
                if len(picked) >= max_twin_pages:
                    break
            for p, kind in picked:
                pid = f"twin_{f[:-4]}_p{p:03d}"
                render(pdf, p, out / "images" / f"{pid}.png")
                degrade(out / "images" / f"{pid}.png", seed=p)
                (out / "gt" / f"{pid}.txt").write_text(page_text(pdf, p), encoding="utf-8")
                rows.append(_meta(pid, "twin", pack, r, p, kind, ref))

        # ---------------- real scans: the two balance-sheet pages
        log = {x["file"]: x for x in _read(pack / "ocr_log.csv")} if (pack / "ocr_log.csv").exists() else {}
        scans = scan_docs or [f for f, x in log.items()
                              if x.get("status") == "ok" and x.get("ocr_pages") == "all"]
        for f in scans:
            if f not in cat or f not in log:
                continue
            r, x = cat[f], log[f]
            ocr_pdf = pack / x["ocr_file"]
            _, ta_p, _ = find_total_assets(ocr_pdf)
            _, tnv_p, _ = find_total_assets(ocr_pdf, label=RE_TNV_LABEL)
            for p, role in sorted({(ta_p, "total_assets"), (tnv_p, "total_resources")} - {(None, "total_assets"), (None, "total_resources")}):
                pid = f"scan_{f[:-4]}_p{p:03d}"
                if any(m["page_id"] == pid for m in rows):
                    continue
                render(pack / "corpus" / f, p, out / "images" / f"{pid}.png")
                m = _meta(pid, "scan", pack, r, p, role, ref)
                m["unit"] = x.get("ocr_unit") or r.get("unit") or ""
                rows.append(m)

    with open(out / "meta.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=META)
        w.writeheader()
        w.writerows(rows)
    z = out.with_suffix(".zip")
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(out.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(out.parent))
    print(f"bench: {len(rows)} pages ({sum(m['set'] == 'twin' for m in rows)} twin, "
          f"{sum(m['set'] == 'scan' for m in rows)} scan) -> {out}  +  {z} "
          f"({z.stat().st_size / 1e6:.1f} MB)")
    return rows


def _meta(pid, set_, pack, r, page, role, ref):
    y, pt = int(r["year"]), r["period_type"]
    q = 0 if pt == "FY" else r["quarter"]
    ta = ref.get((r["ticker"], str(y), str(q)), (None, None))
    return dict(page_id=pid, set=set_, pack=Path(pack).name, file=r["file"], page=page,
                ticker=r["ticker"], year=y, period_type=pt, quarter=r["quarter"],
                scope_raw=r.get("scope_raw", r["scope"]), form=form_of(r["ticker"]),
                accounting="tt99" if y >= 2026 else "tt200", unit=r.get("unit") or "",
                ta_ref=ta[0] if ta[0] and ta[1] == r.get("scope_raw", r["scope"]) else "",
                role=role)
