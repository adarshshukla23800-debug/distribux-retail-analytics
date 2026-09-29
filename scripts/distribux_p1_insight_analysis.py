#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
distribux_p1_insight_analysis.py
=================================
Stage 3: P1 RESULTS ANALYSIS (Data -> Information -> INSIGHT).
Read-only. Runs on your PC. Does NOT recompute P1 from the raw CSVs -
it reads the CSVs that distribux_p1_metrics.py already produced.

This script does NOT write business recommendations. It only answers the
Stage-3 methodological questions with standard, named statistical methods:
  - Is store/product sales concentrated, and by how much? (share-of-total,
    Herfindahl-Hirschman Index, Gini coefficient, Pareto/80-20 point)
  - Is the 2020-2023 trend consistent, or are there outlier periods?
    (year-over-year growth, coefficient of variation, IQR outlier test)
  - Is the return rate concentrated or broadly distributed?
    NOT ANSWERABLE from P1 alone - P1-6 is a single overall number with
    no store/product/category breakdown. This script says so rather than
    guessing, and names it as a P2 candidate.
  - A fixed correlation-vs-causation caution is attached to every trend
    finding, because a growth or decline in SUM(qty x price) has several
    possible explanations (more orders, more stores reporting, price
    changes) that these six P1 metrics cannot distinguish between.

Every finding is labelled:
  DESCRIPTIVE PATTERN   - a fact about the data, safe to state as-is
  REQUIRES INTERPRETATION - a pattern that needs a business explanation
                            this script does not have evidence for
  NOT ANSWERABLE FROM P1 - the required breakdown does not exist yet

USAGE:
    python distribux_p1_insight_analysis.py
    python distribux_p1_insight_analysis.py "C:\\other\\p1_metrics" --out "C:\\other\\out"

Requires Python 3.8+, pandas 1.3+, numpy.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

P1_DIR = r"C:\Users\adars\OneDrive\Desktop\DistribuX\audit_output\p1_metrics"
OUTPUT_DIR = r"C:\Users\adars\OneDrive\Desktop\DistribuX\audit_output\p1_insight_analysis"

REQUIRED_FILES = {
    "total": "p1_total_recorded_gross_sales.csv",
    "store_sales": "p1_sales_by_store.csv",
    "store_orders_units": "p1_orders_units_by_store.csv",
    "product_sales": "p1_sales_by_product.csv",
    "monthly": "p1_sales_trend_monthly.csv",
    "quarterly": "p1_sales_trend_quarterly.csv",
    "annual": "p1_sales_trend_annual.csv",
    "return_rate": "p1_line_return_rate.csv",
}

PARETO_TARGET = 80.0          # classic 80/20 threshold - a named convention, not invented
HHI_UNCONCENTRATED = 1500.0   # US DOJ/FTC merger-guideline convention (0-10000 scale), cited as a reference point only
HHI_MODERATE = 2500.0
IQR_K = 1.5                   # Tukey's standard outlier fence


# --------------------------------------------------------------------------- helpers
def fi(x):
    return "n/a" if x is None else f"{int(round(x)):,}"


def fm(x, d=2):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:,.{d}f}"


def fp(x, d=2):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}f}%"


def load(dirpath, fname):
    p = dirpath / fname
    if not p.exists():
        raise SystemExit(f"ERROR: {fname} not found in {dirpath}. Run distribux_p1_metrics.py first.")
    return pd.read_csv(p, encoding="utf-8-sig")


def hhi(shares_pct):
    """Herfindahl-Hirschman Index on a 0-10000 scale (shares given as 0-100)."""
    return float(np.sum(np.square(shares_pct)))


def gini(values):
    """Standard Gini coefficient (0 = perfectly equal, 1 = maximally concentrated)."""
    x = np.sort(np.asarray(values, dtype=float))
    x = x[~np.isnan(x)]
    n = len(x)
    if n == 0 or x.sum() == 0:
        return None
    cum = np.cumsum(x)
    return float((n + 1 - 2 * (cum.sum() / cum[-1])) / n)


def pareto_point(sorted_desc_values, target_pct=PARETO_TARGET):
    """Minimum number of top items whose cumulative share reaches target_pct of the total."""
    total = float(sorted_desc_values.sum())
    if total <= 0:
        return None
    cum_pct = 100.0 * sorted_desc_values.cumsum() / total
    idx = np.searchsorted(cum_pct.values, target_pct) + 1
    idx = min(idx, len(sorted_desc_values))
    return {"n_items": int(idx), "pct_of_items": 100.0 * idx / len(sorted_desc_values),
            "cum_pct_of_value_at_that_point": float(cum_pct.iloc[idx - 1])}


def iqr_outliers(series, labels):
    s = pd.Series(series).astype(float)
    q1, q3 = s.quantile(0.25), s.quantile(0.75)
    iqr = q3 - q1
    lo, hi = q1 - IQR_K * iqr, q3 + IQR_K * iqr
    mask = (s < lo) | (s > hi)
    return {"q1": float(q1), "q3": float(q3), "iqr": float(iqr), "lower_fence": float(lo), "upper_fence": float(hi),
            "outliers": [(labels[i], float(s.iloc[i])) for i in range(len(s)) if mask.iloc[i]]}


def pct_change_series(values):
    s = pd.Series(values).astype(float)
    return (s.pct_change() * 100.0).tolist()


def linear_trend(y):
    """Simple linear slope/R^2 of the series against its own index. Descriptive only - not a forecast, not causal."""
    y = np.asarray(y, dtype=float)
    x = np.arange(len(y))
    if len(y) < 2 or np.all(y == y[0]):
        return {"slope": 0.0, "r2": 0.0}
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 0.0 if ss_tot == 0 else 1 - ss_res / ss_tot
    return {"slope": float(slope), "r2": float(r2)}


def concentration_block(df, id_col, value_col, label):
    d = df[[id_col, value_col]].dropna().copy()
    d = d[d[value_col] > 0].sort_values(value_col, ascending=False).reset_index(drop=True)
    total = float(d[value_col].sum())
    n = len(d)
    r = {"label": label, "n_items": n, "total": total}
    if n == 0 or total <= 0:
        r["status"] = "NOT ANSWERABLE FROM P1"
        r["reason"] = f"No positive {value_col} rows found in the {label} table."
        return r
    d["share_pct"] = 100.0 * d[value_col] / total
    r["top1_share_pct"] = float(d.share_pct.iloc[0])
    r["top1_id"] = d[id_col].iloc[0]
    r["top3_share_pct"] = float(d.share_pct.iloc[:3].sum())
    r["top5_share_pct"] = float(d.share_pct.iloc[:5].sum())
    r["hhi"] = hhi(d.share_pct.values)
    r["gini"] = gini(d[value_col].values)
    r["pareto"] = pareto_point(d[value_col])
    if r["hhi"] < HHI_UNCONCENTRATED:
        r["hhi_band"] = "unconcentrated (HHI < 1,500)"
    elif r["hhi"] < HHI_MODERATE:
        r["hhi_band"] = "moderately concentrated (1,500-2,500)"
    else:
        r["hhi_band"] = "highly concentrated (HHI > 2,500)"
    r["status"] = "DESCRIPTIVE PATTERN"
    r["_table"] = d
    return r


def trend_block(df, period_col, value_col, label):
    d = df[[period_col, value_col]].dropna().copy()
    if len(d) < 2:
        return {"label": label, "status": "NOT ANSWERABLE FROM P1", "reason": f"Fewer than 2 {label} periods present."}
    try:
        d = d.sort_values(period_col)
    except Exception:
        pass
    vals = d[value_col].astype(float).values
    labels = d[period_col].astype(str).tolist()
    growth = pct_change_series(vals)
    cv = float(np.std(vals, ddof=1) / np.mean(vals)) if np.mean(vals) != 0 and len(vals) > 1 else None
    lt = linear_trend(vals)
    out = iqr_outliers(vals, labels)
    return {"label": label, "status": "DESCRIPTIVE PATTERN", "periods": labels, "values": vals.tolist(),
           "growth_pct": growth, "coefficient_of_variation": cv, "linear_trend": lt, "iqr_outliers": out,
           "min_period": labels[int(np.argmin(vals))], "min_value": float(np.min(vals)),
           "max_period": labels[int(np.argmax(vals))], "max_value": float(np.max(vals))}


# --------------------------------------------------------------------------- report
def build_report(sc, pc, ou_check, trends, ret, data_dir):
    L = []
    w = L.append
    w("# DistribuX Stage 3 - P1 results insight analysis")
    w(f"Run: {dt.datetime.now():%Y-%m-%d %H:%M} | source: `{data_dir}` | pandas {pd.__version__}")
    w("This reads the Stage-2 P1 output CSVs only. It does not touch the raw 12-table dataset and does not "
      "recompute any P1 metric. All findings below are statistical descriptions with a named method; none is a "
      "business recommendation.")

    # ---- store concentration
    w("\n## 1. Store sales concentration (from p1_sales_by_store.csv)")
    w(f"Calculation: share of Recorded Gross Sales per store_id; HHI = sum(share%^2) on a 0-10,000 scale "
      f"(US DOJ/FTC merger-guideline bands used only as a reference point); Gini coefficient (0=equal, "
      f"1=maximally concentrated); Pareto point = minimum stores reaching {PARETO_TARGET:.0f}% of total sales.")
    if sc["status"] == "NOT ANSWERABLE FROM P1":
        w(f"**{sc['status']}**: {sc['reason']}")
    else:
        w(f"- Stores with sales: {fi(sc['n_items'])}; total {fm(sc['total'])}")
        w(f"- Top store ({sc['top1_id']}): {fp(sc['top1_share_pct'])} of total sales")
        w(f"- Top 3 stores: {fp(sc['top3_share_pct'])}; top 5 stores: {fp(sc['top5_share_pct'])}")
        w(f"- HHI = {sc['hhi']:.1f} -> {sc['hhi_band']}")
        w(f"- Gini coefficient = {fm(sc['gini'], 3)}")
        if sc["pareto"]:
            w(f"- Pareto point: {fi(sc['pareto']['n_items'])} store(s) ({fp(sc['pareto']['pct_of_items'])} of all "
              f"stores) account for {fp(sc['pareto']['cum_pct_of_value_at_that_point'])} of total sales")
        w(f"- **{sc['status']}**: this describes how sales are distributed across stores. It does NOT say why "
          "any store is higher or lower (store size, region, product mix, data coverage are all untested "
          "explanations) - **REQUIRES INTERPRETATION** before any resourcing conclusion is drawn.")

    # ---- product concentration
    w("\n## 2. Product sales concentration (from p1_sales_by_product.csv)")
    w("Calculation: identical method to section 1, applied to product_id.")
    if pc["status"] == "NOT ANSWERABLE FROM P1":
        w(f"**{pc['status']}**: {pc['reason']}")
    else:
        w(f"- Products with sales: {fi(pc['n_items'])}; total {fm(pc['total'])}")
        w(f"- Top product ({pc['top1_id']}): {fp(pc['top1_share_pct'])} of total sales")
        w(f"- Top 3 products: {fp(pc['top3_share_pct'])}; top 5 products: {fp(pc['top5_share_pct'])}")
        w(f"- HHI = {pc['hhi']:.1f} -> {pc['hhi_band']}")
        w(f"- Gini coefficient = {fm(pc['gini'], 3)}")
        if pc["pareto"]:
            w(f"- Pareto point: {fi(pc['pareto']['n_items'])} product(s) ({fp(pc['pareto']['pct_of_items'])} of all "
              f"products) account for {fp(pc['pareto']['cum_pct_of_value_at_that_point'])} of total sales")
        w(f"- **{pc['status']}**: a small-N Pareto point (classic 80/20 pattern) is common in retail sales data by "
          "construction and is not, by itself, evidence of a demand insight - **REQUIRES INTERPRETATION**.")

    # ---- orders/units by store cross-check
    w("\n## 3. Orders/units vs sales consistency by store (from p1_orders_units_by_store.csv)")
    w("Calculation: for each store, does a high sales share also mean a high order/unit share, or does the "
      "store differ mainly in average order value? This distinguishes 'more orders' from 'bigger orders' as "
      "possible explanations for a sales difference - it does not resolve which one is the cause.")
    if ou_check["status"] == "NOT ANSWERABLE FROM P1":
        w(f"**{ou_check['status']}**: {ou_check['reason']}")
    else:
        w(f"- Correlation (sales share vs order-count share across stores): r = {fm(ou_check['corr_sales_orders'], 3)}")
        w(f"- Average recorded sales per order, by store: min {fm(ou_check['aov_min'])} "
          f"({ou_check['aov_min_store']}), max {fm(ou_check['aov_max'])} ({ou_check['aov_max_store']})")
        w(f"- **REQUIRES INTERPRETATION**: {ou_check['note']}")

    # ---- trends
    for key, title, note in (
        ("monthly", "4. Monthly trend (2020-2023)", "Best for spotting short outlier periods; noisiest granularity."),
        ("quarterly", "5. Quarterly trend (2020-2023)", "Smooths monthly noise; a reasonable balance for a report chart."),
        ("annual", "6. Annual trend (2020-2023)", "Coarsest view; year-over-year growth only, 4 data points."),
    ):
        t = trends[key]
        w(f"\n## {title}")
        w(f"Calculation: growth% = period-over-period % change in Recorded Gross Sales; coefficient of variation "
          f"(CV) = std/mean across periods; linear trend = ordinary least-squares slope of sales against period "
          f"index (a descriptive line-of-best-fit, not a forecast); outliers = Tukey's IQR rule "
          f"(< Q1-{IQR_K}*IQR or > Q3+{IQR_K}*IQR). {note}")
        if t["status"] == "NOT ANSWERABLE FROM P1":
            w(f"**{t['status']}**: {t['reason']}")
            continue
        w(f"- Periods: {len(t['periods'])} ({t['periods'][0]} to {t['periods'][-1]})")
        w(f"- Lowest: {t['min_period']} = {fm(t['min_value'])}; Highest: {t['max_period']} = {fm(t['max_value'])}")
        w(f"- Coefficient of variation: {fm(t['coefficient_of_variation'], 4) if t['coefficient_of_variation'] is not None else 'n/a'}")
        w(f"- Linear trend slope: {fm(t['linear_trend']['slope'])} per period, R^2 = {fm(t['linear_trend']['r2'], 3)} "
          f"({'a straight line explains most of the period-to-period movement' if t['linear_trend']['r2'] > 0.6 else 'a straight line explains only part of the movement - the series is not simply monotonic'})")
        nz = [g for g in t["growth_pct"][1:] if g is not None and not np.isnan(g)]
        if nz:
            w(f"- Period-over-period growth range: {fm(min(nz))}% to {fm(max(nz))}%")
        if t["iqr_outliers"]["outliers"]:
            w(f"- Outlier period(s) (Tukey's rule, fence {fm(t['iqr_outliers']['lower_fence'])} to "
              f"{fm(t['iqr_outliers']['upper_fence'])}): " +
              "; ".join(f"{p} = {fm(v)}" for p, v in t["iqr_outliers"]["outliers"]))
            w("- **REQUIRES INTERPRETATION**: an outlier period is a statistical fact, not an explained one; do "
              "not attribute it to a business cause not evidenced elsewhere in the dataset.")
        else:
            w("- No statistical outlier periods found by Tukey's rule.")
        w("- **CORRELATION-VS-CAUSATION CAUTION**: this trend is SUM(qty x order_items.price) over time. P1 "
          "does not track order count, active store count, or pricing changes over time separately, so a rising "
          "or falling total could reflect more orders, more active stores, or price changes - not necessarily "
          "'growing demand.' Decomposing this is a P2 candidate, not a P1 conclusion.")

    # ---- return rate
    w("\n## 7. Return rate distribution")
    w("Calculation: attempted a distribution question (is the return rate concentrated by store/product/category?) "
      "against p1_line_return_rate.csv.")
    w(f"- P1-6 provides a single **overall** figure: {fi(ret['numerator'])} / {fi(ret['denominator'])} = "
      f"{fp(ret['rate_pct'])}. No store, product or category breakdown exists in the P1 outputs.")
    w("- **NOT ANSWERABLE FROM P1**: whether returns are concentrated or broadly distributed requires a "
      "return-rate-by-store/category/product metric, which is a named P2 candidate in the Business Analytics "
      "Blueprint, not a P1 metric. This script does not estimate or approximate that breakdown.")

    # ---- classification summary
    w("\n## Classification: real pattern vs data artifact")
    w("| # | Question | Finding | Status |\n|---|---|---|---|")
    def row(n, q, f, s):
        w(f"| {n} | {q} | {f} | **{s}** |")
    row(1, "Is store sales concentrated?",
        (f"Top store = {fp(sc.get('top1_share_pct'))}, HHI = {sc.get('hhi', 0):.0f} ({sc.get('hhi_band', 'n/a')})"
         if sc["status"] != "NOT ANSWERABLE FROM P1" else "no data"), sc["status"])
    row(2, "Are a few products dominating sales?",
        (f"Top {pc['pareto']['n_items'] if pc.get('pareto') else '?'} products = "
         f"{fp(pc['pareto']['cum_pct_of_value_at_that_point']) if pc.get('pareto') else 'n/a'} of sales"
         if pc["status"] != "NOT ANSWERABLE FROM P1" else "no data"), pc["status"])
    row(3, "Are sales growing consistently 2020-2023?",
        (f"Slope {fm(trends['annual']['linear_trend']['slope']) if trends['annual']['status'] != 'NOT ANSWERABLE FROM P1' else 'n/a'}, "
         f"R^2 {fm(trends['annual']['linear_trend']['r2'], 3) if trends['annual']['status'] != 'NOT ANSWERABLE FROM P1' else 'n/a'}"),
        "REQUIRES INTERPRETATION" if trends["annual"]["status"] != "NOT ANSWERABLE FROM P1" else "NOT ANSWERABLE FROM P1")
    m_ok = trends["monthly"]["status"] != "NOT ANSWERABLE FROM P1"
    m_out = trends["monthly"]["iqr_outliers"]["outliers"] if m_ok else []
    row(4, "Are there unusual periods?",
        (", ".join(f"{p}" for p, _ in m_out[:5]) or "none flagged") if m_ok else "no data",
        "NOT ANSWERABLE FROM P1" if not m_ok else ("REQUIRES INTERPRETATION" if m_out else "DESCRIPTIVE PATTERN"))
    row(5, "Are return rates concentrated or broadly distributed?", "single overall figure only, no breakdown", "NOT ANSWERABLE FROM P1")
    row(6, "Correlation vs causation risk on the sales trend?", "present on every trend finding above", "REQUIRES INTERPRETATION")

    # ---- goal check / gate
    w("\n## Goal check")
    w("This script answers the Stage-3 questions using named, standard statistical methods only (share-of-total, "
      "HHI, Gini, Pareto point, Tukey IQR outliers, OLS trend slope/R^2). No finding above is stated as a business "
      "cause; every concentration and trend finding is explicitly marked as requiring interpretation before it "
      "becomes an insight, and the return-rate distribution question is marked not answerable from P1 rather than "
      "estimated.")
    w("\n## Final gate")
    w("PASS - proceed to a scoped decision on which REQUIRES INTERPRETATION findings are worth carrying into "
      "Stage 4 (P2), and whether return-rate-by-store/category/product is worth adding as a P2 metric to answer "
      "question 5 above. Do not move to Stage 5 (business insight layer) or Stage 6 (visualization) until that "
      "decision is made.")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- MAIN
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    out_arg = None
    if "--out" in args:
        i = args.index("--out")
        out_arg = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    data_dir = Path(args[0] if args else P1_DIR)
    out_dir = Path(out_arg) if out_arg else Path(OUTPUT_DIR)
    if not data_dir.exists():
        raise SystemExit(f"ERROR: folder not found: {data_dir}. Run distribux_p1_metrics.py first.")
    print(f"DistribuX Stage 3 - P1 insight analysis\n  source: {data_dir}\n  out   : {out_dir}\n")

    dfs = {}
    for key, fname in REQUIRED_FILES.items():
        dfs[key] = load(data_dir, fname)
        print(f"  loaded {fname}: {len(dfs[key]):,} row(s)")

    sc = concentration_block(dfs["store_sales"], "store_id", "recorded_gross_sales", "store")
    pc = concentration_block(dfs["product_sales"], "product_id", "recorded_gross_sales", "product")

    # orders/units-by-store cross-check (needs store sales joined to store orders/units)
    ou = dfs["store_orders_units"].merge(dfs["store_sales"], on="store_id", how="inner")
    if len(ou) < 2 or ou.recorded_gross_sales.sum() <= 0 or ou.orders.sum() <= 0:
        ou_check = {"status": "NOT ANSWERABLE FROM P1", "reason": "fewer than 2 stores with both sales and order data"}
    else:
        ou["sales_share"] = 100.0 * ou.recorded_gross_sales / ou.recorded_gross_sales.sum()
        ou["order_share"] = 100.0 * ou.orders / ou.orders.sum()
        ou["aov"] = ou.recorded_gross_sales / ou.orders.replace(0, np.nan)
        corr = float(np.corrcoef(ou.sales_share, ou.order_share)[0, 1]) if ou.sales_share.std() > 0 and ou.order_share.std() > 0 else None
        note = ("Sales share tracks order-count share closely (stores differ mainly by how many orders they take)"
               if (corr is not None and corr > 0.8) else
               "Sales share does NOT track order-count share closely (stores differ in average order value, not "
               "just order count) - a high-sales store is not necessarily a high-order-count store")
        ou_check = {"status": "DESCRIPTIVE PATTERN", "corr_sales_orders": corr,
                   "aov_min": float(ou.aov.min()), "aov_min_store": ou.loc[ou.aov.idxmin(), "store_id"],
                   "aov_max": float(ou.aov.max()), "aov_max_store": ou.loc[ou.aov.idxmax(), "store_id"], "note": note}

    trends = {
        "monthly": trend_block(dfs["monthly"], "month", "recorded_gross_sales", "monthly"),
        "quarterly": trend_block(dfs["quarterly"], "quarter", "recorded_gross_sales", "quarterly"),
        "annual": trend_block(dfs["annual"], "year", "recorded_gross_sales", "annual"),
    }

    rr = dfs["return_rate"].iloc[0]
    ret = {"numerator": float(rr.numerator_distinct_returned_lines), "denominator": float(rr.denominator_distinct_order_lines),
          "rate_pct": float(rr.line_level_return_rate_pct)}

    out_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(sc, pc, ou_check, trends, ret, data_dir)
    (out_dir / "p1_insight_analysis_report.md").write_text(report, encoding="utf-8")
    if "_table" in sc:
        sc["_table"].to_csv(out_dir / "store_concentration_detail.csv", index=False, encoding="utf-8-sig")
    if "_table" in pc:
        pc["_table"].to_csv(out_dir / "product_concentration_detail.csv", index=False, encoding="utf-8-sig")

    print("\n" + report)
    print(f"Done. Report: {out_dir / 'p1_insight_analysis_report.md'}")
    print("Bring this report back for the Stage 3 decision on what (if anything) moves into Stage 4 (P2).")


if __name__ == "__main__":
    main()
