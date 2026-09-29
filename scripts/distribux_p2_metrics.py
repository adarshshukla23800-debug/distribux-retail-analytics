#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
distribux_p2_metrics.py
========================
Stage 4: TARGETED P2 analytics. Read-only. Does NOT repeat the forensic
audit or Stage 2/3. Builds only the four P2 metrics selected after the
Stage 3 gate, because they either reveal structure at a coarser grain
than P1 (category, supplier) or close a gap Stage 3 explicitly flagged
as NOT ANSWERABLE FROM P1 (return rate by group; customer spend).

Metrics:
  P2-1  Recorded Sales by Category      (order_items -> products, M:1)
  P2-2  Recorded Sales by Supplier      (order_items -> products, M:1)
  P2-3  Line-Level Return Rate by Store / Category / Product
        (numerator: DISTINCT returned order_item_id, per group - never
         COUNT(*) of return rows, per the Stage-2 anomaly finding of
         731 multi-return lines)
  P2-4  Recorded Spend per Customer     (order_items -> orders -> customers)

Design decisions (same discipline as Stage 2):
  - Every join here is many-to-one (order_items -> products/orders is
    always many lines to one parent row), so none of these joins can
    fan out or duplicate an order_items row.
  - No 1:N child table (payments, shipments, returns) is joined into
    more than one other table in the same computation.
  - Line-less orders (no order_items row) are excluded from P2-4 by
    construction, same as in P1-2/P1-3 - not labelled cancelled/unpaid.

USAGE:
    python distribux_p2_metrics.py
    python distribux_p2_metrics.py "C:\\path\\to\\data" --out "C:\\path\\to\\out"

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
OUTPUT_DIR = r"C:\Users\adars\OneDrive\Desktop\DistribuX\audit_output\p2_metrics"

NEEDED = {
    "order_items.csv": ["order_item_id", "order_id", "product_id", "qty", "price"],
    "orders.csv": ["order_id", "store_id", "customer_id"],
    "products.csv": ["product_id", "category_id", "supplier_id"],
    "returns.csv": ["return_id", "order_item_id"],
}
ANCHOR_GROSS_SALES = 3827746136
ANCHOR_RETURNED_LINES = 29251
ANCHOR_ITEMS_ROWS = 600000
ENCODINGS = ["utf-8-sig", "cp1252", "latin-1"]
EPS = 0.5
HHI_UNCONCENTRATED, HHI_MODERATE = 1500.0, 2500.0
PARETO_TARGET = 80.0


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


def fp(a, b=None, d=4):
    if b is None:
        return "n/a" if a is None or (isinstance(a, float) and np.isnan(a)) else f"{a:.{d}f}%"
    return "n/a" if not b else f"{100.0 * a / b:.{d}f}%"


def key(s):
    k = s.astype(str).str.strip().str.replace(r"\.0+$", "", regex=True)
    return k.mask(s.isna() | k.eq("") | k.str.lower().eq("nan"))


def find_file(data_dir, fname):
    for p in data_dir.iterdir():
        if p.is_file() and p.name.lower() == fname.lower():
            return p
    return None


def load_csv(path, needed_cols):
    for enc in ENCODINGS:
        try:
            hdr = pd.read_csv(path, nrows=0, encoding=enc, dtype=str)
            colmap = {c.strip().lower(): c for c in hdr.columns}
            missing = [c for c in needed_cols if c not in colmap]
            if missing:
                raise SystemExit(f"ERROR: {path.name} is missing required column(s) {missing}. "
                                 f"Columns found: {list(hdr.columns)}")
            df = pd.read_csv(path, usecols=[colmap[c] for c in needed_cols], dtype=str,
                             keep_default_na=False, na_values=[""], encoding=enc)
            return df.rename(columns={colmap[c]: c for c in needed_cols})[needed_cols]
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"ERROR: could not decode {path.name} with any of {ENCODINGS}")


def hhi(shares_pct):
    return float(np.sum(np.square(shares_pct)))


def gini(values):
    x = np.sort(np.asarray(values, dtype=float))
    x = x[~np.isnan(x)]
    n = len(x)
    if n == 0 or x.sum() == 0:
        return None
    cum = np.cumsum(x)
    return float((n + 1 - 2 * (cum.sum() / cum[-1])) / n)


def pareto_point(sorted_desc_values, target_pct=PARETO_TARGET):
    total = float(sorted_desc_values.sum())
    if total <= 0:
        return None
    cum_pct = 100.0 * sorted_desc_values.cumsum() / total
    idx = min(int(np.searchsorted(cum_pct.values, target_pct)) + 1, len(sorted_desc_values))
    return {"n_items": int(idx), "pct_of_items": 100.0 * idx / len(sorted_desc_values),
            "cum_pct_of_value_at_that_point": float(cum_pct.iloc[idx - 1])}


def concentration_stats(values_desc):
    total = float(values_desc.sum())
    shares = 100.0 * values_desc / total
    return {"n_items": len(values_desc), "total": total, "top1_share_pct": float(shares.iloc[0]),
            "top3_share_pct": float(shares.iloc[:3].sum()), "hhi": hhi(shares.values),
            "gini": gini(values_desc.values), "pareto": pareto_point(values_desc)}


def hhi_band(h):
    return ("unconcentrated (HHI < 1,500)" if h < HHI_UNCONCENTRATED else
            "moderately concentrated (1,500-2,500)" if h < HHI_MODERATE else "highly concentrated (HHI > 2,500)")


LOG = []


def log_metric(metric, formula, grain, tables, join, result, status, note):
    LOG.append({"metric": metric, "formula": formula, "grain": grain, "tables": tables,
               "join": join, "result": result, "status": status, "note": note})


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
    print(f"DistribuX P2 metrics (targeted)\n  data: {data_dir}\n  out : {out_dir}\n")

    paths = {}
    for fname in NEEDED:
        p = find_file(data_dir, fname)
        if p is None:
            raise SystemExit(f"ERROR: {fname} not found in {data_dir}")
        paths[fname] = p
    before_hash = {n: sha256_file(p) for n, p in paths.items()}

    items_raw = load_csv(paths["order_items.csv"], NEEDED["order_items.csv"])
    orders_raw = load_csv(paths["orders.csv"], NEEDED["orders.csv"])
    products_raw = load_csv(paths["products.csv"], NEEDED["products.csv"])
    returns_raw = load_csv(paths["returns.csv"], NEEDED["returns.csv"])
    for fname, df in (("order_items.csv", items_raw), ("orders.csv", orders_raw),
                      ("products.csv", products_raw), ("returns.csv", returns_raw)):
        print(f"  loaded {fname}: {len(df):,} rows")

    I = pd.DataFrame({"line_id": key(items_raw.order_item_id), "order_id": key(items_raw.order_id),
                      "product_id": key(items_raw.product_id),
                      "qty": pd.to_numeric(items_raw.qty, errors="coerce"),
                      "price": pd.to_numeric(items_raw.price, errors="coerce")})
    I["sales"] = I.qty * I.price
    O = pd.DataFrame({"order_id": key(orders_raw.order_id), "store_id": key(orders_raw.store_id),
                      "customer_id": key(orders_raw.customer_id)})
    P = pd.DataFrame({"product_id": key(products_raw.product_id), "category_id": key(products_raw.category_id),
                      "supplier_id": key(products_raw.supplier_id)})
    R = pd.DataFrame({"line_id": key(returns_raw.order_item_id)})

    # CHALLENGE - checks relevant to these 4 metrics
    issues = []
    dup_products = int(P.product_id.duplicated().sum())
    if dup_products:
        issues.append(f"products.product_id has {dup_products:,} duplicate values.")
    valid_order_ids = set(O.order_id.dropna())
    orphan_mask = I.order_id.notna() & ~I.order_id.isin(valid_order_ids)
    n_orphan = int(orphan_mask.sum())
    if n_orphan:
        issues.append(f"{n_orphan:,} order_items rows reference an order_id not in orders.csv "
                      f"(excluded from P2-4, which requires the orders join).")
    valid_product_ids = set(P.product_id.dropna())
    orphan_prod_mask = I.product_id.notna() & ~I.product_id.isin(valid_product_ids)
    n_orphan_prod = int(orphan_prod_mask.sum())
    if n_orphan_prod:
        issues.append(f"{n_orphan_prod:,} order_items rows reference a product_id not in products.csv "
                      f"(excluded from P2-1/P2-2, which require the products join).")
    print("\nCHALLENGE - data-quality checks relevant to these 4 metrics:")
    for m in issues:
        print(f"  - {m}")
    if not issues:
        print("  - none: product_id is unique in products.csv; all order_items rows join cleanly.")

    total_sales = float(I.sales.fillna(0).sum())

    # =========================================================================
    # P2-1: Recorded Sales by Category
    # =========================================================================
    JP = I.merge(P[["product_id", "category_id", "supplier_id"]], on="product_id", how="inner")
    by_cat = JP.groupby("category_id", dropna=False).sales.sum(min_count=1).fillna(0).reset_index()
    by_cat.columns = ["category_id", "recorded_gross_sales"]
    by_cat = by_cat.sort_values("recorded_gross_sales", ascending=False)
    cat_sum = float(by_cat.recorded_gross_sales.sum())
    expected_joined = float(I.loc[~orphan_prod_mask, "sales"].fillna(0).sum())
    cat_ok = abs(cat_sum - expected_joined) <= EPS
    d = by_cat[by_cat.recorded_gross_sales > 0].recorded_gross_sales
    cat_conc = concentration_stats(d) if len(d) else None
    note1 = (f"Sum across categories = {fm(cat_sum)}; expected (lines with a valid product_id) = {fm(expected_joined)} -> "
            f"{'identity holds' if cat_ok else 'MISMATCH'}. " +
            (f"Concentration: top category = {fp(cat_conc['top1_share_pct'])}, HHI = {cat_conc['hhi']:.1f} "
             f"({hhi_band(cat_conc['hhi'])}), Gini = {fm(cat_conc['gini'], 3)}." if cat_conc else ""))
    log_metric("P2-1 Recorded Sales by Category", "SUM(qty x price) GROUP BY products.category_id",
              "order line -> product -> category", "order_items, products", "order_items -> products (M:1)",
              cat_sum, "VERIFIED" if cat_ok else "UNRESOLVED", note1)
    print(f"\nP2-1 Sales by Category: {len(by_cat)} categories; sum = {fm(cat_sum)}  [{'OK' if cat_ok else 'MISMATCH'}]")
    if cat_conc:
        print(f"  concentration: top={fp(cat_conc['top1_share_pct'])}, HHI={cat_conc['hhi']:.1f} ({hhi_band(cat_conc['hhi'])})")

    # =========================================================================
    # P2-2: Recorded Sales by Supplier
    # =========================================================================
    by_sup = JP.groupby("supplier_id", dropna=False).sales.sum(min_count=1).fillna(0).reset_index()
    by_sup.columns = ["supplier_id", "recorded_gross_sales"]
    by_sup = by_sup.sort_values("recorded_gross_sales", ascending=False)
    sup_sum = float(by_sup.recorded_gross_sales.sum())
    sup_ok = abs(sup_sum - expected_joined) <= EPS
    d2 = by_sup[by_sup.recorded_gross_sales > 0].recorded_gross_sales
    sup_conc = concentration_stats(d2) if len(d2) else None
    note2 = (f"Sum across suppliers = {fm(sup_sum)}; expected = {fm(expected_joined)} -> "
            f"{'identity holds' if sup_ok else 'MISMATCH'}. " +
            (f"Concentration: top supplier = {fp(sup_conc['top1_share_pct'])}, HHI = {sup_conc['hhi']:.1f} "
             f"({hhi_band(sup_conc['hhi'])}), Gini = {fm(sup_conc['gini'], 3)}." if sup_conc else ""))
    log_metric("P2-2 Recorded Sales by Supplier", "SUM(qty x price) GROUP BY products.supplier_id",
              "order line -> product -> supplier", "order_items, products", "order_items -> products (M:1), reused from P2-1",
              sup_sum, "VERIFIED" if sup_ok else "UNRESOLVED", note2)
    print(f"P2-2 Sales by Supplier: {len(by_sup)} suppliers; sum = {fm(sup_sum)}  [{'OK' if sup_ok else 'MISMATCH'}]")
    if sup_conc:
        print(f"  concentration: top={fp(sup_conc['top1_share_pct'])}, HHI={sup_conc['hhi']:.1f} ({hhi_band(sup_conc['hhi'])})")

    # =========================================================================
    # P2-3: Line-Level Return Rate by Store / Category / Product
    # Numerator per group = DISTINCT returned order_item_id (never COUNT(*) of
    # return rows - the 731 multi-return lines from Stage 2 would overstate it).
    # =========================================================================
    valid_line_ids = set(I.line_id.dropna())
    ret_lines = R.line_id.dropna()
    ret_in_scope = set(ret_lines[ret_lines.isin(valid_line_ids)])
    I["is_returned"] = I.line_id.isin(ret_in_scope)
    denom_lines_total = int(I.line_id.dropna().nunique())
    numerator_total = len(ret_in_scope)
    rate_overall = numerator_total / denom_lines_total if denom_lines_total else None
    anchor_ok = numerator_total == ANCHOR_RETURNED_LINES and denom_lines_total == ANCHOR_ITEMS_ROWS

    # by product (no join needed - product_id is native to order_items)
    by_prod_ret = I.groupby("product_id", dropna=False).agg(
        lines=("line_id", "nunique"), returned=("is_returned", "sum")).reset_index()
    by_prod_ret["return_rate_pct"] = 100.0 * by_prod_ret.returned / by_prod_ret.lines.replace(0, np.nan)
    by_prod_ret = by_prod_ret.sort_values("return_rate_pct", ascending=False)

    # by category (reuse JP, the same order_items->products join as P2-1)
    JP["is_returned"] = JP.line_id.isin(ret_in_scope)
    by_cat_ret = JP.groupby("category_id", dropna=False).agg(
        lines=("line_id", "nunique"), returned=("is_returned", "sum")).reset_index()
    by_cat_ret["return_rate_pct"] = 100.0 * by_cat_ret.returned / by_cat_ret.lines.replace(0, np.nan)
    by_cat_ret = by_cat_ret.sort_values("return_rate_pct", ascending=False)

    # by store (order_items -> orders, M:1, independent of the products join above)
    JO = I.merge(O[["order_id", "store_id"]], on="order_id", how="inner")
    by_store_ret = JO.groupby("store_id", dropna=False).agg(
        lines=("line_id", "nunique"), returned=("is_returned", "sum")).reset_index()
    by_store_ret["return_rate_pct"] = 100.0 * by_store_ret.returned / by_store_ret.lines.replace(0, np.nan)
    by_store_ret = by_store_ret.sort_values("return_rate_pct", ascending=False)

    # DEVIL'S ADVOCATE: distinct returned lines summed across each grouping must equal
    # the overall numerator (a line belongs to exactly one product/category/store).
    prod_num_sum = int(by_prod_ret.returned.sum())
    cat_num_sum = int(by_cat_ret.returned.sum())
    store_num_sum = int(by_store_ret[by_store_ret.store_id.notna()].returned.sum()) + \
        int(by_store_ret[by_store_ret.store_id.isna()].returned.sum())
    prod_ok = prod_num_sum == numerator_total
    cat_ok2 = cat_num_sum == numerator_total  # may differ slightly if some lines have no valid product_id
    store_ok = store_num_sum == int(JO.is_returned.sum())  # store grouping scoped to joined lines only

    rate_min_store = by_store_ret.loc[by_store_ret.lines >= 30, "return_rate_pct"].min() if (by_store_ret.lines >= 30).any() else None
    rate_max_store = by_store_ret.loc[by_store_ret.lines >= 30, "return_rate_pct"].max() if (by_store_ret.lines >= 30).any() else None
    rate_min_cat = by_cat_ret.return_rate_pct.min()
    rate_max_cat = by_cat_ret.return_rate_pct.max()

    note3 = (f"Overall: {fi(numerator_total)}/{fi(denom_lines_total)} = {fp(rate_overall * 100 if rate_overall else None)} "
            f"(matches Stage-2 anchor 29,251/600,000: {'yes' if anchor_ok else 'NO'}). "
            f"Cross-checks: distinct returned lines summed across products = {fi(prod_num_sum)} "
            f"({'matches' if prod_ok else 'does NOT match'} overall numerator {fi(numerator_total)}); "
            f"across categories = {fi(cat_num_sum)} ({'matches' if cat_ok2 else 'differs - lines with no valid product_id are excluded from the category grouping'}); "
            f"across stores (lines with a resolvable order) = {fi(store_num_sum)} "
            f"({'matches' if store_ok else 'does NOT match'} the store-joined numerator {fi(int(JO.is_returned.sum()))}). "
            f"Store return rate range (stores with >=30 lines, to avoid small-sample noise): "
            f"{fp(rate_min_store)} to {fp(rate_max_store)}. Category return rate range: {fp(rate_min_cat)} to {fp(rate_max_cat)}. "
            f"This answers the Stage-3 'NOT ANSWERABLE FROM P1' gap: return rates are compared across groups here for the "
            f"first time.")
    log_metric("P2-3 Line-Level Return Rate by Store/Category/Product",
              "COUNT(DISTINCT returned order_item_id) / COUNT(DISTINCT order_item_id), grouped",
              "order line, grouped by store / category / product", "order_items, returns, orders, products",
              "order_items -> orders (store); order_items -> products (category) - each a separate M:1 join, never combined",
              rate_overall, "VERIFIED" if (anchor_ok and prod_ok) else "UNRESOLVED", note3)
    print(f"P2-3 Return rate by group: overall {fp(rate_overall * 100 if rate_overall else None)} "
          f"[{'MATCH anchor' if anchor_ok else 'ANCHOR MISMATCH'}]; "
          f"store range {fp(rate_min_store)}-{fp(rate_max_store)}; category range {fp(rate_min_cat)}-{fp(rate_max_cat)}")

    # =========================================================================
    # P2-4: Recorded Spend per Customer
    # =========================================================================
    JC = I.merge(O[["order_id", "customer_id"]], on="order_id", how="inner")  # reuses same join pattern as P1-2
    order_sales = JC.groupby("order_id").agg(order_sales=("sales", lambda s: float(s.fillna(0).sum())),
                                             customer_id=("customer_id", "first")).reset_index()
    by_cust = order_sales.groupby("customer_id").agg(
        orders=("order_id", "nunique"), total_spend=("order_sales", "sum")).reset_index()
    by_cust["avg_order_value"] = by_cust.total_spend / by_cust.orders
    by_cust = by_cust.sort_values("total_spend", ascending=False)
    cust_sum = float(by_cust.total_spend.sum())
    expected_cust = float(I.loc[~orphan_mask, "sales"].fillna(0).sum())  # same scope as P1-2 (orders-joinable lines)
    cust_ok = abs(cust_sum - expected_cust) <= EPS
    dc = by_cust[by_cust.total_spend > 0].total_spend
    cust_conc = concentration_stats(dc) if len(dc) else None
    note4 = (f"Sum across customers = {fm(cust_sum)}; expected (lines whose order was found) = {fm(expected_cust)} -> "
            f"{'identity holds' if cust_ok else 'MISMATCH'}. Excludes line-less orders by construction (same as P1-2), "
            f"not labelled unpaid/cancelled. " +
            (f"Concentration: top customer = {fp(cust_conc['top1_share_pct'])}, HHI = {cust_conc['hhi']:.1f} "
             f"({hhi_band(cust_conc['hhi'])}), Gini = {fm(cust_conc['gini'], 3)}. Orders per customer: "
             f"min {fi(by_cust.orders.min())}, median {fi(by_cust.orders.median())}, max {fi(by_cust.orders.max())}."
             if cust_conc else ""))
    log_metric("P2-4 Recorded Spend per Customer", "SUM(qty x price) per order, summed per customer",
              "order line -> order -> customer", "order_items, orders", "order_items -> orders (M:1), reused pattern from P1-2",
              cust_sum, "VERIFIED" if cust_ok else "UNRESOLVED", note4)
    print(f"P2-4 Spend per Customer: {len(by_cust)} customers; sum = {fm(cust_sum)}  [{'OK' if cust_ok else 'MISMATCH'}]")
    if cust_conc:
        print(f"  concentration: top={fp(cust_conc['top1_share_pct'])}, HHI={cust_conc['hhi']:.1f} ({hhi_band(cust_conc['hhi'])})")

    # =========================================================================
    any_unresolved = any(m["status"] == "UNRESOLVED" for m in LOG)
    goal_check = ("All four P2 metrics reuse the same M:1 join discipline as P1, no 1:N child table is combined "
                 "with another in one computation, and P2-3 closes the exact gap Stage 3 flagged as unanswerable. " +
                 ("One or more metrics is UNRESOLVED - investigate before use." if any_unresolved else
                  "No correction was necessary; final gate PASSES."))

    out_dir.mkdir(parents=True, exist_ok=True)
    by_cat.to_csv(out_dir / "p2_sales_by_category.csv", index=False, encoding="utf-8-sig")
    by_sup.to_csv(out_dir / "p2_sales_by_supplier.csv", index=False, encoding="utf-8-sig")
    by_prod_ret.to_csv(out_dir / "p2_return_rate_by_product.csv", index=False, encoding="utf-8-sig")
    by_cat_ret.to_csv(out_dir / "p2_return_rate_by_category.csv", index=False, encoding="utf-8-sig")
    by_store_ret.to_csv(out_dir / "p2_return_rate_by_store.csv", index=False, encoding="utf-8-sig")
    by_cust.to_csv(out_dir / "p2_spend_by_customer.csv", index=False, encoding="utf-8-sig")

    after_hash = {n: sha256_file(p) for n, p in paths.items()}
    source_unchanged = before_hash == after_hash

    report = build_report(issues, goal_check, data_dir, source_unchanged, cat_conc, sup_conc, cust_conc,
                          rate_overall, denom_lines_total, numerator_total, anchor_ok,
                          rate_min_store, rate_max_store, rate_min_cat, rate_max_cat, by_cust)
    (out_dir / "p2_metrics_report.md").write_text(report, encoding="utf-8")

    print(f"\nSource files unchanged: {source_unchanged}")
    print(f"Final gate: {'PASS' if not any_unresolved else 'REVIEW NEEDED'}")
    print(f"\nOutputs written to: {out_dir}")


def build_report(issues, goal_check, data_dir, source_unchanged, cat_conc, sup_conc, cust_conc,
                 rate_overall, denom, num, anchor_ok, rmin_s, rmax_s, rmin_c, rmax_c, by_cust):
    L = []
    w = L.append
    w("# DistribuX P2 metrics report (targeted, Stage 4)")
    w(f"Run: {dt.datetime.now():%Y-%m-%d %H:%M} | data: `{data_dir}` | pandas {pd.__version__}")
    w(f"Source files unchanged: **{'YES' if source_unchanged else 'NO - INVESTIGATE'}**")
    w("\n## CHALLENGE")
    for m in issues:
        w(f"- {m}")
    if not issues:
        w("- None: product_id is a unique key; all order_items rows join cleanly to products and orders.")
    w("\n## Metrics")
    w("| Metric | Formula | Grain | Table(s) | Join | Result | Status |\n|---|---|---|---|---|--:|---|")
    for m in LOG:
        if isinstance(m["result"], float) and m["result"] <= 1.0 and "rate" in m["metric"].lower():
            res = fp(m["result"] * 100)  # this row's result is a rate (0-1 fraction), not a money sum
        elif isinstance(m["result"], float):
            res = fm(m["result"])
        else:
            res = "see CSV"
        w(f"| {m['metric']} | {m['formula']} | {m['grain']} | {m['tables']} | {m['join']} | {res} | **{m['status']}** |")
    w("\n## Notes and cross-checks")
    for m in LOG:
        w(f"**{m['metric']}**\n- {m['note']}")
    w("\n## Business-readable summary")
    w(f"- **Category concentration**: top category = {fp(cat_conc['top1_share_pct'])} of sales, HHI = {cat_conc['hhi']:.0f} "
      f"({hhi_band(cat_conc['hhi'])}) -> same near-uniform pattern seen in P1 store/product concentration, now confirmed "
      f"at category grain too." if cat_conc else "- Category concentration: no data.")
    w(f"- **Supplier concentration**: top supplier = {fp(sup_conc['top1_share_pct'])} of sales, HHI = {sup_conc['hhi']:.0f} "
      f"({hhi_band(sup_conc['hhi'])})." if sup_conc else "- Supplier concentration: no data.")
    w(f"- **Return rate distribution** (the Stage-3 gap): overall {fp(rate_overall * 100 if rate_overall else None)} "
      f"({fi(num)}/{fi(denom)}, {'matches' if anchor_ok else 'DOES NOT MATCH'} the Stage-2 anchor). Across stores with "
      f"a meaningful sample (>=30 lines), the rate ranges {fp(rmin_s)} to {fp(rmax_s)}; across categories, "
      f"{fp(rmin_c)} to {fp(rmax_c)}. {'This range is narrow relative to the overall rate, consistent with the same near-uniform pattern seen elsewhere - REQUIRES INTERPRETATION before concluding any store/category is a genuine outlier.' if rmax_s and rmin_s and (rmax_s - rmin_s) < 3 else 'This range is wide enough to warrant a closer look at the highest/lowest group - REQUIRES INTERPRETATION, not yet a conclusion.'}")
    w(f"- **Customer spend concentration**: top customer = {fp(cust_conc['top1_share_pct'])} of recorded spend, "
      f"HHI = {cust_conc['hhi']:.0f} ({hhi_band(cust_conc['hhi'])}); median orders per customer = "
      f"{fi(by_cust.orders.median())}." if cust_conc else "- Customer spend: no data.")
    w("\n## Goal check")
    w(goal_check)
    w("\n## Final gate")
    w("PASS" if all(m["status"] != "UNRESOLVED" for m in LOG) else "REVIEW NEEDED - see UNRESOLVED metric(s) above.")
    w("\n## Terminology")
    w("- Recorded Gross Sales, not Revenue. Recorded spend, not confirmed cash receipt.")
    w("- No profit, margin, COGS, inventory turnover, delivery time, employee performance, or promotion ROI claim is made.")
    w("\n## Files produced")
    for f in ("p2_sales_by_category.csv", "p2_sales_by_supplier.csv", "p2_return_rate_by_product.csv",
             "p2_return_rate_by_category.csv", "p2_return_rate_by_store.csv", "p2_spend_by_customer.csv",
             "p2_metrics_report.md (this file)"):
        w(f"- {f}")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
