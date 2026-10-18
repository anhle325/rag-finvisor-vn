"""reference_financials.csv from CafeF's structured summary — the evaluation answer key.

    GET https://cafef.vn/du-lieu/Ajax/PageNew/FinanceReport.ashx
        ?Type=1|2 &Symbol=VNM &TotalRow=N &EndDate=<Q-YYYY | YYYY> &ReportType=QUY|NAM &Sort=DESC

Type 1 = income-statement summary, Type 2 = balance-sheet summary.
EndDate is "4-2025" for quarters and "2025" for years (anything else answers
"Time sai dinh dang").

Three traps, each checked on real data (VNM, Sept 2026):

1. **Unit = nghìn đồng.** VNM Q4/2025 revenue comes back as 17,045,421,379
   → 17.0 trillion đồng once multiplied by 1,000. Not đồng, not triệu đồng.
2. **"Doanh thu bán hàng và CCDV" is line 01 (GROSS revenue), not line 10.**
   Revenue − COGS = 6,901,885,966 but the reported gross profit is 6,890,016,993;
   the 11.9 bn gap is the revenue deductions (line 02). Net revenue (line 10) is
   therefore derived as COGS + gross profit, and flagged as derived.
3. **Banks use different codes** (TotalIncome, TienChoVay, TienGui, VonQui...),
   and the summary covers ONE scope per company: consolidated (ReportType H / HK)
   when it has subsidiaries, otherwise its standalone report (N / K).
"""

from __future__ import annotations

import csv
from pathlib import Path

BASE = "https://cafef.vn/du-lieu/Ajax/PageNew/FinanceReport.ashx"
UNIT = 1000                      # nghìn đồng -> đồng

CORP_MAP = {"DTTBHCCDV": "gross_revenue_01", "GV": "cogs", "LNGBHCCDV": "gross_profit",
            "LNTC": "financial_profit", "LNK": "other_profit", "TotalProfit": "pretax_profit",
            "LNSTTNDN": "net_profit", "NetIncome": "net_profit_parent",
            "ShortTermFloatingCapital": "current_assets", "TotalAsset": "total_assets",
            "TotalShortTermDebt": "current_liabilities", "TotalDebt": "total_liabilities",
            "TotalOwnerCapital": "total_equity"}
BANK_MAP = {"TotalIncome": "bank_total_income", "TongChiPhi": "bank_total_expense",
            "TienChoVay": "bank_loans", "DauTuCK": "bank_securities",
            "GopVonDTDaiHan": "bank_lt_investments", "TienGui": "bank_deposits",
            "VonQui": "total_equity"}
COLUMNS = ["ticker", "year", "quarter", "scope", "report_type_raw", "audit_note",
           "net_revenue", "net_revenue_derived", "gross_revenue_01", "cogs", "gross_profit",
           "pretax_profit", "net_profit", "net_profit_parent", "total_assets",
           "total_liabilities", "total_equity", "current_assets", "current_liabilities",
           "bank_total_income", "bank_loans", "bank_deposits", "unit", "source",
           "ref_flags", "total_assets_source_value"]


def _query(client, ticker, typ, report_type, end, n):
    url = (f"{BASE}?Type={typ}&Symbol={ticker.lower()}&TotalRow={n}&EndDate={end}"
           f"&ReportType={report_type}&Sort=DESC")
    j = client.get_json(url, headers={"Referer": "https://cafef.vn/"}) or {}
    if not j.get("Success"):
        return []
    return ((j.get("Data") or {}).get("Value")) or []


def fetch_reference(client, ticker, years, long_rows=None):
    """Return wide rows (one per ticker x period) in đồng."""
    years = sorted(set(years))
    last = max(years)
    n_q, n_y = 4 * (last - min(years) + 1), last - min(years) + 1
    rows = {}
    for typ in (1, 2):
        for rtype, end, n in (("QUY", f"4-{last}", n_q), ("NAM", f"{last}", n_y)):
            for period in _query(client, ticker, typ, rtype, end, n):
                y, q = int(period["Year"]), int(period.get("Quater") or 0)
                if y not in years:
                    continue
                key = (y, q if rtype == "QUY" else 0)
                row = rows.setdefault(key, dict(
                    ticker=ticker.upper(), year=y, quarter=key[1],
                    # H / HK = hợp nhất (HK: đã kiểm toán); N / K = company without
                    # subsidiaries (observed for SHS, DHG) -> its standalone report
                    scope="hopnhat" if str(period.get("ReportType", "")).startswith("H")
                    else "standalone",
                    report_type_raw=period.get("ReportType", ""),
                    audit_note=(period.get("Conten") or "").strip(), unit="dong",
                    source="cafef:FinanceReport"))
                for item in period.get("Value") or []:
                    code = item.get("Code")
                    col = CORP_MAP.get(code) or BANK_MAP.get(code)
                    val = item.get("Value")
                    if long_rows is not None:
                        long_rows.append(dict(ticker=ticker.upper(), year=y, quarter=key[1],
                                              statement=typ, code=code, name=item.get("Name"),
                                              value_thousand=val))
                    if col and val is not None:
                        row[col] = int(round(float(val) * UNIT))
    for row in rows.values():
        if row.get("cogs") is not None and row.get("gross_profit") is not None:
            row["net_revenue"] = row["cogs"] + row["gross_profit"]
            row["net_revenue_derived"] = True
        sanity_check(row)
    return [rows[k] for k in sorted(rows)]


def sanity_check(row):
    """The answer key needs checking too.

    From 2026 filings follow Thông tư 99/2025/TT-BTC, which renumbers the balance
    sheet: 'Tổng cộng tài sản' moves from code 270 to 280, and 270 becomes 'Tài sản
    dài hạn khác'. CafeF's summary still reads code 270, so its 2026 'Tổng tài sản' is
    really 'other long-term assets' (observed: GVR Q1/2026 6.2 tn vs 87.2 tn in the
    PDF; POW, HPG, FPT, GAS... — 56 of the top-50 rows for 2026). The balance-sheet
    identity catches it: total assets must equal liabilities + equity."""
    ta, li, eq = row.get("total_assets"), row.get("total_liabilities"), row.get("total_equity")
    flags = []
    if ta and li and eq and abs(ta - (li + eq)) > 0.01 * (li + eq):
        row["total_assets_source_value"] = ta
        row["total_assets"] = li + eq
        flags.append("total_assets_replaced_by_liabilities_plus_equity")
    if row.get("current_assets") and row.get("total_assets") and \
            row["current_assets"] > row["total_assets"] * 1.001:
        flags.append("current_assets_gt_total_assets")
    row["ref_flags"] = ";".join(flags)
    return row


def build_reference(client, tickers, years, out_dir, verbose=True):
    out_dir = Path(out_dir)
    wide, long_rows = [], []
    for t in tickers:
        try:
            r = fetch_reference(client, t, years, long_rows)
        except Exception as e:                                  # noqa: BLE001
            print(f"  reference {t}: FAILED ({type(e).__name__}: {e})")
            continue
        wide.extend(r)
        if verbose:
            print(f"  reference {t}: {len(r)} periods")
    with open(out_dir / "reference_financials.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(wide)
    with open(out_dir / "reference_long.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["ticker", "year", "quarter", "statement", "code",
                                           "name", "value_thousand"])
        w.writeheader()
        w.writerows(long_rows)
    return wide
