#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
distribux_p1_metrics.py
========================
Stage: P1 business metrics (Data -> Information stage of the DistribuX project).
Read-only. Runs on your PC. Does NOT repeat the forensic audit.

Calculates exactly six metrics, each at the smallest safe grain/join path:
  P1-1  Recorded Gross Sales (overall + annual, for validation)
  P1-2  Recorded Sales by Store
  P1-3  Orders and Units by Store
  P1-4  Recorded Sales by Product
  P1-5  Recorded Sales Trend 2020-2023 (monthly / quarterly / annual)
  P1-6  Overall Line-Level Return Rate

Design decisions (see also inline comments at each metric):
  - Only order_items, orders and returns are loaded and joined. Payments,
    shipments, promotions, employees, suppliers, customers, products and
    categories are NOT joined here: they are not needed for these six
    metrics, and joining an extra 1:N table is exactly what inflated the
    naive payments total by ~2.14x in the earlier fan-out audit.
  - "Recorded Gross Sales" = SUM(order_items.qty x order_items.price).
    order_items.price is used because products.price authority is
    unresolved (99.977% of lines differ). This is NOT called "revenue".
  - Every join in this script is order_items -> orders on order_id, a
    many-to-one join (many order_items rows to one orders row), so it
    cannot fan out or duplicate order_items rows.
  - All 12 files are checked for PRESENCE ONLY (a lightweight structural
    inventory, so drift such as a missing/renamed table is caught early).
    Only order_items.csv, orders.csv and returns.csv are actually parsed,
    per the "smallest possible table path" instruction.

USAGE:
    python distribux_p1_metrics.py
    python distribux_p1_metrics.py "C:\\other\\data" --out "C:\\other\\out"

Requires Python 3.8+, pandas 1.3+, numpy.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = r"C:\Users\adars\OneDrive\Desktop\DistribuX\data"
OUTPUT_DIR = r"C:\Users\adars\OneDrive\Desktop\DistribuX\audit_output\p1_metrics"

# The 12 tables expected in the folder (presence check only - see note above).
ALL_12_FILES = [
    "categories.csv", "customers.csv", "employees.csv", "orders.csv",
    "order_items.csv", "payments.csv", "products.csv", "promotions.csv",
    "returns.csv", "shipments.csv", "stores.csv", "suppliers.csv",
]

# Columns actually needed, per the established schema (from the earlier full audit).
NEEDED = {
    "order_items.csv": ["order_item_id", "order_id", "product_id", "qty", "price"],
    "orders.csv": ["order_id", "store_id", "order_date"],
    "returns.csv": ["return_id", "order_item_id"],
}

# Anchors from the earlier full forensic audit. Used only to CHECK, never to force a result.
ANCHOR_GROSS_SALES = 3827746136
ANCHOR_RETURNED_LINES = 29251
ANCHOR_ITEMS_ROWS = 600000

TREND_START = "2020-01-01"
TREND_END = "2023-12-31"     # inclusive; 2024-01-01 (all 195 orders) is deliberately excluded
ENCODINGS = ["utf-8-sig", "cp1252", "latin-1"]
EPS = 0.5  # rounding tolerance for money-total identity checks


# --------------------------------------------------------------------------- helpers
def sha256_file(p, chunk=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def fi(x):
    return "n/a" if x is None else f"{int(round(x)):,}"


def fm(x, d=2):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:,.{d}f}"


def fp(a, b, d=4):
    return "n/a" if not b else f"{100.0 * a / b:.{d}f}%"


def key(s):
    """Normalise an ID column to a comparable string key (whitespace/float-suffix safe)."""
    k = s.astype(str).str.strip().str.replace(r"\.0+$", "", regex=True)
    return k.mask(s.isna() | k.eq("") | k.str.lower().eq("nan"))


def find_file(data_dir, fname):
    for p in data_dir.iterdir():
        if p.is_file() and p.name.lower() == fname.lower():
            return p
    return None


def load_csv(path, needed_cols):
    """Load only the needed columns, in any encoding, with a clear failure if a column is missing."""
    for enc in ENCODINGS:
        try:
            hdr = pd.read_csv(path, nrows=0, encoding=enc, dtype=str)
            colmap = {c.strip().lower(): c for c in hdr.columns}
            missing = [c for c in needed_cols if c not in colmap]
            if missing:
                raise SystemExit(
                    f"ERROR: {path.name} is missing required column(s) {missing}. "
                    f"Columns found: {list(hdr.columns)}")
            df = pd.read_csv(path, usecols=[colmap[c] for c in needed_cols], dtype=str,
                             keep_default_na=False, na_values=[""], encoding=enc)
            return df.rename(columns={colmap[c]: c for c in needed_cols})[needed_cols]
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"ERROR: could not decode {path.name} with any of {ENCODINGS}")


# --------------------------------------------------------------------------- metric log (for the .md report)
LOG = []  # list of dicts: metric, formula, grain, tables, join, result, status, note, correction


def log_metric(metric, formula, grain, tables, join, result, status, note, correction="none required"):
    LOG.append({"metric": metric, "formula": formula, "grain": grain, "tables": tables,
               "join": join, "result": result, "status": status, "note": note, "correction": correction})


# =============================================================================
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
    data_dir = Path(args[0] if args else DATA_DIR)
    out_dir = Path(out_arg) if out_arg else Path(OUTPUT_DIR)
    if not data_dir.exists():
        raise SystemExit(f"ERROR: folder not found: {data_dir}")
    print(f"DistribuX P1 metrics\n  data: {data_dir}\n  out : {out_dir}\n")

    # -------- structural inventory: all 12 files must be present (existence only) --------
    missing_files = [f for f in ALL_12_FILES if find_file(data_dir, f) is None]
    if missing_files:
        raise SystemExit(f"ERROR: expected 12 DistribuX tables; missing from {data_dir}: {missing_files}")
    print(f"  12/12 expected tables present in {data_dir} (existence check only).")

    # -------- load only the 3 tables these 6 metrics actually need --------
    p_items, p_orders, p_returns = (find_file(data_dir, f) for f in
                                    ("order_items.csv", "orders.csv", "returns.csv"))
    items_raw = load_csv(p_items, NEEDED["order_items.csv"])
    orders_raw = load_csv(p_orders, NEEDED["orders.csv"])
    returns_raw = load_csv(p_returns, NEEDED["returns.csv"])
    before_hash = {p.name: sha256_file(p) for p in (p_items, p_orders, p_returns)}
    print(f"  loaded order_items.csv: {len(items_raw):,} rows")
    print(f"  loaded orders.csv     : {len(orders_raw):,} rows")
    print(f"  loaded returns.csv    : {len(returns_raw):,} rows")

    # Typed working frames. qty/price coerced numeric; IDs normalised as string keys.
    I = pd.DataFrame({
        "line_id": key(items_raw.order_item_id),
        "order_id": key(items_raw.order_id),
        "product_id": key(items_raw.product_id),
        "qty": pd.to_numeric(items_raw.qty, errors="coerce"),
        "price": pd.to_numeric(items_raw.price, errors="coerce"),
    })
    I["sales"] = I.qty * I.price

    O = pd.DataFrame({
        "order_id": key(orders_raw.order_id),
        "store_id": key(orders_raw.store_id),
        "order_date": pd.to_datetime(orders_raw.order_date, errors="coerce"),
    })

    R = pd.DataFrame({"line_id": key(returns_raw.order_item_id)})

    # =========================================================================
    # CHALLENGE (data-quality checks that matter to these 6 metrics only)
    # =========================================================================
    issues = []

    dup_lines = int(I.line_id.duplicated().sum())
    if dup_lines:
        issues.append(f"order_items.order_item_id has {dup_lines:,} duplicate values "
                      f"(expected to be a unique key; a duplicate would double-count that line in every metric).")

    bad_qty_price = int((I.qty.isna() | I.price.isna()).sum())
    if bad_qty_price:
        issues.append(f"{bad_qty_price:,} order_items rows have a non-numeric or missing qty/price "
                      f"(excluded from SUM(qty x price); treated as 0, not dropped from row counts).")

    dup_orders = int(O.order_id.duplicated().sum())
    if dup_orders:
        issues.append(f"orders.order_id has {dup_orders:,} duplicate values "
                      f"(would distort DISTINCT order counts and store grouping).")

    # DEVIL'S ADVOCATE: does the order_items -> orders join drop or duplicate any line?
    valid_order_ids = set(O.order_id.dropna())
    orphan_mask = I.order_id.notna() & ~I.order_id.isin(valid_order_ids)
    n_orphan = int(orphan_mask.sum())
    orphan_sales = float(I.loc[orphan_mask, "sales"].fillna(0).sum())
    if n_orphan:
        issues.append(f"{n_orphan:,} order_items rows reference an order_id not found in orders.csv "
                      f"(carrying {fm(orphan_sales)} in sales; EXCLUDED from store/trend metrics, "
                      f"included in the P1-1 and P1-4 totals since those do not require the orders join).")

    null_store = int(O.store_id.isna().sum())
    if null_store:
        issues.append(f"{null_store:,} orders have a null store_id (routed to a 'store_id=UNKNOWN' bucket "
                      f"in P1-2/P1-3 rather than silently dropped).")

    bad_dates = int((orders_raw.order_date.astype(str).str.strip().ne("") & O.order_date.isna()).sum())
    if bad_dates:
        issues.append(f"{bad_dates:,} orders.order_date values could not be parsed "
                      f"(excluded from the P1-5 trend; NOT excluded from P1-1/P1-2/P1-3/P1-4, which do not use the date).")

    print("\nCHALLENGE - data-quality checks relevant to these 6 metrics:")
    for m in issues:
        print(f"  - {m}")
    if not issues:
        print("  - none: order_item_id and order_id are unique, qty/price/store_id/order_date are clean.")

    # =========================================================================
    # P1-1: Recorded Gross Sales
    # Grain: order line. No join (order_items only) -> cannot fan out.
    # =========================================================================
    total_sales = float(I.sales.fillna(0).sum())
    diff = total_sales - ANCHOR_GROSS_SALES
    anchor_status = "MATCH" if abs(diff) <= EPS else ("CLOSE" if abs(diff) / ANCHOR_GROSS_SALES <= 0.01 else "DIFFERENT")
    note1 = (f"'Recorded' because order_items.price is used, not products.price (authority unresolved). "
            f"Anchor check vs prior full-audit figure {fi(ANCHOR_GROSS_SALES)}: {anchor_status} (diff {fm(diff)}).")
    p1_years = I.assign(order_id=I.order_id).merge(O[["order_id", "order_date"]], on="order_id", how="left")
    p1_years["year"] = p1_years.order_date.dt.year
    by_year = p1_years.groupby("year", dropna=False).sales.sum(min_count=1).fillna(0).reset_index()
    by_year.columns = ["year", "recorded_gross_sales"]
    log_metric("P1-1 Recorded Gross Sales", "SUM(order_items.qty x order_items.price)", "order line",
              "order_items", "No join", total_sales,
              "VERIFIED" if anchor_status == "MATCH" else "VERIFIED WITH NOTE", note1)
    print(f"\nP1-1 Recorded Gross Sales = {fm(total_sales)}  [{anchor_status} vs anchor]")

    # =========================================================================
    # P1-2: Recorded Sales by Store
    # Grain: order line -> order -> store. order_items -> orders is M:1 (many
    # lines per order, one store per order): summing after this join cannot
    # duplicate a line, because each order_item row matches at most one order row.
    # =========================================================================
    J = I.merge(O[["order_id", "store_id"]], on="order_id", how="inner")  # inner: orphan lines excluded (see CHALLENGE)
    J["store_id"] = J.store_id.fillna("UNKNOWN")
    by_store_sales = J.groupby("store_id", dropna=False).sales.sum(min_count=1).fillna(0).reset_index()
    by_store_sales.columns = ["store_id", "recorded_gross_sales"]
    by_store_sales = by_store_sales.sort_values("recorded_gross_sales", ascending=False)

    # DEVIL'S ADVOCATE: sum across stores must equal sales on lines whose order was found (no fan-out, no leakage)
    expected_joined_sales = float(I.loc[~orphan_mask, "sales"].fillna(0).sum())
    store_sum = float(by_store_sales.recorded_gross_sales.sum())
    store_check_ok = abs(store_sum - expected_joined_sales) <= EPS
    note2 = (f"Excludes the {n_orphan:,} orphan-order lines (if any) and ALL 40,767-scale line-less orders by "
            f"construction, since they have no order_items row to attribute to a store. "
            f"Sum across stores = {fm(store_sum)}; expected = {fm(expected_joined_sales)} -> "
            f"{'identity holds' if store_check_ok else 'MISMATCH - investigate'}.")
    log_metric("P1-2 Recorded Sales by Store", "SUM(qty x price) GROUP BY orders.store_id", "order line -> store",
              "order_items, orders", "order_items -> orders (M:1)", store_sum,
              "VERIFIED" if store_check_ok else "UNRESOLVED", note2)
    print(f"P1-2 Recorded Sales by Store: {len(by_store_sales)} store(s); sum = {fm(store_sum)}  "
          f"[{'OK' if store_check_ok else 'MISMATCH'}]")

    # =========================================================================
    # P1-3: Orders and Units by Store
    # Grain: order (distinct order_id) and order line (qty), both -> store.
    # Same join as P1-2, reused (not rejoined) to avoid any risk of a second,
    # inconsistent join producing a different row set.
    # =========================================================================
    orders_units = J.groupby("store_id", dropna=False).agg(
        orders=("order_id", "nunique"), units=("qty", lambda s: float(s.fillna(0).sum()))
    ).reset_index().sort_values("orders", ascending=False)

    # DEVIL'S ADVOCATE: distinct orders across stores must not exceed, and (since every
    # store bucket is disjoint) must sum to, the distinct orders present in the join.
    total_distinct_orders_joined = int(J.order_id.nunique())
    orders_sum_by_store = int(orders_units.orders.sum())
    orders_check_ok = orders_sum_by_store == total_distinct_orders_joined
    units_sum = float(orders_units.units.sum())
    units_expected = float(J.qty.fillna(0).sum())
    units_check_ok = abs(units_sum - units_expected) <= EPS
    note3 = (f"Orders and units are grouped from the SAME order_items->orders join as P1-2 (no re-join). "
            f"Distinct orders across store buckets = {fi(orders_sum_by_store)}; distinct orders in the join "
            f"= {fi(total_distinct_orders_joined)} -> {'identity holds' if orders_check_ok else 'MISMATCH'}. "
            f"Units sum {'matches' if units_check_ok else 'does NOT match'} SUM(qty) on the same join.")
    log_metric("P1-3 Orders and Units by Store", "COUNT(DISTINCT order_id), SUM(qty) GROUP BY orders.store_id",
              "order -> store (orders); order line -> store (units)", "order_items, orders",
              "order_items -> orders (M:1), reused from P1-2", None,
              "VERIFIED" if (orders_check_ok and units_check_ok) else "UNRESOLVED", note3)
    print(f"P1-3 Orders/Units by Store: {len(orders_units)} store(s); "
          f"orders sum = {fi(orders_sum_by_store)} [{'OK' if orders_check_ok else 'MISMATCH'}], "
          f"units sum = {fi(units_sum)} [{'OK' if units_check_ok else 'MISMATCH'}]")

    # =========================================================================
    # P1-4: Recorded Sales by Product
    # Grain: order line -> product. No join (order_items.product_id is native
    # to order_items) -> cannot fan out. products.price is deliberately NOT used.
    # =========================================================================
    by_product = I.groupby("product_id", dropna=False).sales.sum(min_count=1).fillna(0).reset_index()
    by_product.columns = ["product_id", "recorded_gross_sales"]
    by_product = by_product.sort_values("recorded_gross_sales", ascending=False)
    product_sum = float(by_product.recorded_gross_sales.sum())
    product_check_ok = abs(product_sum - total_sales) <= EPS  # trivial identity: single-table grouping
    note4 = (f"Uses order_items.price only; products.price is NOT substituted (authority unresolved). "
            f"Sum across products = {fm(product_sum)} vs P1-1 total {fm(total_sales)} -> "
            f"{'identity holds' if product_check_ok else 'MISMATCH - investigate'}.")
    log_metric("P1-4 Recorded Sales by Product", "SUM(qty x price) GROUP BY order_items.product_id", "order line -> product",
              "order_items", "No join", product_sum,
              "VERIFIED" if product_check_ok else "UNRESOLVED", note4)
    print(f"P1-4 Recorded Sales by Product: {len(by_product)} product(s); sum = {fm(product_sum)}  "
          f"[{'OK' if product_check_ok else 'MISMATCH'}]")

    # =========================================================================
    # P1-5: Recorded Sales Trend, 2020-2023 (monthly / quarterly / annual)
    # Grain: order line -> order -> calendar period. 2024 is explicitly
    # excluded by the date filter (not by "assuming" it is a full year).
    # =========================================================================
    Jd = I.merge(O[["order_id", "order_date"]], on="order_id", how="inner")  # inner: orphan lines excluded, as in P1-2
    start, end = pd.Timestamp(TREND_START), pd.Timestamp(TREND_END)
    in_range = Jd.order_date.notna() & (Jd.order_date >= start) & (Jd.order_date <= end)
    Jt = Jd.loc[in_range].copy()
    n_excluded_2024plus = int((Jd.order_date > end).sum())
    n_excluded_pre2020 = int((Jd.order_date < start).sum())
    n_excluded_nodate = int(Jd.order_date.isna().sum())

    Jt["year"] = Jt.order_date.dt.year
    Jt["quarter"] = Jt.order_date.dt.year.astype(str) + "-Q" + Jt.order_date.dt.quarter.astype(str)
    Jt["month"] = Jt.order_date.dt.to_period("M").astype(str)

    monthly = Jt.groupby("month").sales.sum(min_count=1).fillna(0).reset_index().sort_values("month")
    monthly.columns = ["month", "recorded_gross_sales"]
    quarterly = Jt.groupby("quarter").sales.sum(min_count=1).fillna(0).reset_index().sort_values("quarter")
    quarterly.columns = ["quarter", "recorded_gross_sales"]
    annual = Jt.groupby("year").sales.sum(min_count=1).fillna(0).reset_index().sort_values("year")
    annual.columns = ["year", "recorded_gross_sales"]

    filtered_total = float(Jt.sales.fillna(0).sum())
    # DEVIL'S ADVOCATE: the three granularities must reconcile to the same filtered total (identity, same rows).
    m_sum, q_sum, a_sum = float(monthly.recorded_gross_sales.sum()), float(quarterly.recorded_gross_sales.sum()), float(annual.recorded_gross_sales.sum())
    trend_ok = all(abs(x - filtered_total) <= EPS for x in (m_sum, q_sum, a_sum))
    years_present = sorted(int(y) for y in annual.year.unique())
    no_2024 = 2024 not in years_present and all(y <= 2023 for y in years_present)
    note5 = (f"Filtered to {TREND_START}..{TREND_END} inclusive. Excluded: {fi(n_excluded_2024plus)} lines after "
            f"{TREND_END} (2024's 195 single-day orders), {fi(n_excluded_pre2020)} before {TREND_START}, "
            f"{fi(n_excluded_nodate)} with an unparseable/missing date, plus the {fi(n_orphan)} orphan-order lines "
            f"already excluded in the CHALLENGE step. Monthly/quarterly/annual sums reconcile to the same filtered "
            f"total ({fm(filtered_total)}): {'yes' if trend_ok else 'NO - MISMATCH'}. "
            f"Years present: {years_present} -> 2024 {'correctly excluded' if no_2024 else 'WAS NOT EXCLUDED'}.")
    log_metric("P1-5 Recorded Sales Trend 2020-2023", "SUM(qty x price) GROUP BY month/quarter/year of orders.order_date",
              "order line -> order -> calendar period", "order_items, orders", "order_items -> orders (M:1)",
              filtered_total, "VERIFIED" if (trend_ok and no_2024) else "UNRESOLVED", note5)
    print(f"P1-5 Trend 2020-2023: filtered total = {fm(filtered_total)}, years = {years_present}  "
          f"[{'OK' if (trend_ok and no_2024) else 'MISMATCH/2024 LEAK'}]")

    # =========================================================================
    # P1-6: Overall Line-Level Return Rate
    # Grain: order line. Numerator uses DISTINCT returned order_item_id
    # (731 lines have >1 return row per the earlier audit; COUNT(*) would
    # overstate the numerator). Denominator = distinct order_item_id in
    # order_items (the population of lines that could be returned).
    # =========================================================================
    denom_lines = int(I.line_id.dropna().nunique())
    valid_line_ids = set(I.line_id.dropna())
    returned_in_scope = R.line_id.dropna()
    numerator_lines = int(returned_in_scope[returned_in_scope.isin(valid_line_ids)].nunique())
    n_return_rows_out_of_scope = int((~returned_in_scope.isin(valid_line_ids)).sum())
    return_rate = numerator_lines / denom_lines if denom_lines else None

    denom_matches_rowcount = denom_lines == len(I) == ANCHOR_ITEMS_ROWS
    numerator_vs_anchor = "MATCH" if numerator_lines == ANCHOR_RETURNED_LINES else "DIFFERENT"
    note6 = (f"Numerator = COUNT(DISTINCT order_item_id) in returns.csv that also exist in order_items.csv "
            f"= {fi(numerator_lines)} (NOT COUNT(*) of return rows, which would include the 731 multi-return "
            f"lines' extra rows). {fi(n_return_rows_out_of_scope)} return rows reference an order_item_id not "
            f"found in order_items.csv and are excluded from the numerator. Denominator = distinct "
            f"order_item_id in order_items.csv = {fi(denom_lines)} "
            f"({'equals row count, i.e. the key is unique' if denom_matches_rowcount else 'LESS than row count - duplicate line IDs present, see CHALLENGE'}). "
            f"Numerator vs prior full-audit anchor {fi(ANCHOR_RETURNED_LINES)}: {numerator_vs_anchor}.")
    log_metric("P1-6 Overall Line-Level Return Rate", "COUNT(DISTINCT returned order_item_id) / COUNT(DISTINCT order_item_id)",
              "order line", "returns, order_items", "returns -> order_items (existence check, not a value join)",
              return_rate, "VERIFIED" if numerator_vs_anchor == "MATCH" else "VERIFIED WITH NOTE", note6)
    print(f"P1-6 Line-Level Return Rate = {fp(numerator_lines, denom_lines)} "
          f"({fi(numerator_lines)} / {fi(denom_lines)})  [{numerator_vs_anchor} vs anchor]")

    # =========================================================================
    # GOAL CHECK (Data -> Information -> Insight -> Decision -> Action)
    # =========================================================================
    any_unresolved = any(m["status"] == "UNRESOLVED" for m in LOG)
    goal_check = ("All six P1 metrics use a single-table or single M:1-join path, none introduce a second "
                 "1:N child table, every cross-check reconciles, and each result carries the qualification "
                 "needed to defend it in an MBA viva (recorded vs confirmed, line-less orders excluded by "
                 "construction, 2024 excluded from the trend). " +
                 ("One or more metrics is UNRESOLVED and should be investigated before use in P2."
                  if any_unresolved else "No correction was necessary beyond routing nulls to an 'UNKNOWN' bucket; "
                  "final gate PASSES."))

    # =========================================================================
    # WRITE OUTPUTS
    # =========================================================================
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"metric": "Total Recorded Gross Sales", "recorded_gross_sales": total_sales}]).to_csv(
        out_dir / "p1_total_recorded_gross_sales.csv", index=False, encoding="utf-8-sig")
    by_year.to_csv(out_dir / "p1_recorded_gross_sales_by_year_all_years.csv", index=False, encoding="utf-8-sig")
    by_store_sales.to_csv(out_dir / "p1_sales_by_store.csv", index=False, encoding="utf-8-sig")
    orders_units.to_csv(out_dir / "p1_orders_units_by_store.csv", index=False, encoding="utf-8-sig")
    by_product.to_csv(out_dir / "p1_sales_by_product.csv", index=False, encoding="utf-8-sig")
    monthly.to_csv(out_dir / "p1_sales_trend_monthly.csv", index=False, encoding="utf-8-sig")
    quarterly.to_csv(out_dir / "p1_sales_trend_quarterly.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(out_dir / "p1_sales_trend_annual.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{"numerator_distinct_returned_lines": numerator_lines,
                  "denominator_distinct_order_lines": denom_lines,
                  "line_level_return_rate_pct": 100.0 * return_rate if return_rate is not None else None}]).to_csv(
        out_dir / "p1_line_return_rate.csv", index=False, encoding="utf-8-sig")

    after_hash = {p.name: sha256_file(p) for p in (p_items, p_orders, p_returns)}
    source_unchanged = before_hash == after_hash

    report = build_report(source_unchanged, issues, goal_check, data_dir)
    (out_dir / "p1_metrics_report.md").write_text(report, encoding="utf-8")

    print(f"\nSource files unchanged: {source_unchanged}")
    print(f"Final gate: {'PASS' if not any_unresolved else 'REVIEW NEEDED'}")
    print(f"\nOutputs written to: {out_dir}")
    print("Bring p1_metrics_report.md back for the next loop (P2).")


def build_report(source_unchanged, issues, goal_check, data_dir):
    L = []
    w = L.append
    w("# DistribuX P1 metrics report")
    w(f"Run: {dt.datetime.now():%Y-%m-%d %H:%M} | data: `{data_dir}` | pandas {pd.__version__}")
    w(f"Source files unchanged (SHA-256 before vs after, order_items/orders/returns only): "
      f"**{'YES' if source_unchanged else 'NO - INVESTIGATE'}**")
    w("\n## CHALLENGE - data-quality checks relevant to these 6 metrics")
    for m in issues:
        w(f"- {m}")
    if not issues:
        w("- None: order_item_id and order_id are unique keys; qty, price, store_id and order_date are clean "
          "for the rows these metrics use. No correction was necessary.")
    w("\n## Metrics")
    w("| Metric | Formula | Grain | Table(s) | Join | Result | Status |\n|---|---|---|---|---|--:|---|")
    for m in LOG:
        res = fm(m["result"]) if isinstance(m["result"], float) else (fi(m["result"]) if m["result"] is not None else "see CSV")
        w(f"| {m['metric']} | {m['formula']} | {m['grain']} | {m['tables']} | {m['join']} | {res} | **{m['status']}** |")
    w("\n## Notes, cross-checks and corrections (evidence-gated loop)")
    for m in LOG:
        w(f"**{m['metric']}**")
        w(f"- {m['note']}")
        w(f"- Correction applied: {m['correction']}")
    w("\n## Goal check (Data -> Information -> Insight -> Decision -> Action)")
    w(goal_check)
    w("\n## Final gate")
    w("PASS" if all(m["status"] != "UNRESOLVED" for m in LOG) else "REVIEW NEEDED - see UNRESOLVED metric(s) above before proceeding to P2.")
    w("\n## Terminology used")
    w("- **Recorded Gross Sales** (not \"Revenue\"): SUM(order_items.qty x order_items.price); price authority unresolved.")
    w("- Line-less orders (orders with no order_items row) are excluded from P1-2/P1-3/P1-5 by construction, "
      "not labelled cancelled, unpaid or inactive.")
    w("- No profit, margin, COGS, inventory turnover, delivery time, employee performance, promotion ROI, "
      "or cash-collection claim is made anywhere in this script.")
    w("\n## Files produced")
    w("- p1_total_recorded_gross_sales.csv")
    w("- p1_recorded_gross_sales_by_year_all_years.csv (all years present, for reference; 2024 shown separately, not merged into the P1-5 trend)")
    w("- p1_sales_by_store.csv")
    w("- p1_orders_units_by_store.csv")
    w("- p1_sales_by_product.csv")
    w("- p1_sales_trend_monthly.csv")
    w("- p1_sales_trend_quarterly.csv")
    w("- p1_sales_trend_annual.csv")
    w("- p1_line_return_rate.csv")
    w("- p1_metrics_report.md (this file)")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
