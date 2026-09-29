#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
distribux_followup_checks.py
============================
TARGETED evidence check (NOT a new full audit). Read-only. Runs on your PC.

Five checks only:
  1. Refund exposure            (returns.csv: repeated order_item_id)
  2. Refund vs original line value (returns -> order_items, qty x price)
  3. order_items.price vs products.price
  4. Recorded payment amount on line-less orders
  5. 2024 daily orders

Reads ONLY the columns it needs from 5 files:
  returns.csv, order_items.csv, products.csv, orders.csv, payments.csv

USAGE:
    python distribux_followup_checks.py
    python distribux_followup_checks.py "C:\\other\\data" --out "C:\\other\\out"

Requires Python 3.8+, pandas 1.3+, numpy.
Source CSVs are opened read-only; SHA-256 hashes before/after prove they are unchanged.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = r"C:\Users\adars\OneDrive\Desktop\DistribuX\data"
OUTPUT_DIR = r"C:\Users\adars\OneDrive\Desktop\DistribuX\audit_output"

# Anchors = values from the earlier full audit. Used ONLY to confirm the local
# data is the same data (a mismatch is reported, never forced).
PRIOR = {
    "return_rows": 30000,
    "returned_distinct_lines": 29251,
    "refund_total": 75962300,
    "lineless_orders": 40767,
    "payments_total": 3018080810,
    "gross_sales": 3827746136,
    "orders_2024": 195,
}

FILES = {
    "returns": ("returns.csv", ["return_id", "order_item_id", "refund"]),
    "items": ("order_items.csv", ["order_item_id", "order_id", "product_id", "qty", "price"]),
    "products": ("products.csv", ["product_id", "price"]),
    "orders": ("orders.csv", ["order_id", "order_date"]),
    "payments": ("payments.csv", ["order_id", "amount"]),
}
ENCODINGS = ["utf-8-sig", "cp1252", "latin-1"]
OK, ANOM, UNRES = "VERIFIED", "ANOMALY / REQUIRES INTERPRETATION", "UNRESOLVED"
EPS = 1e-9


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
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:,.{d}f}"


def fp(a, b, d=3):
    return "n/a" if not b else f"{100.0 * a / b:.{d}f}%"


def pc(a, b):
    return None if not b else 100.0 * a / b


def clean_json(o):
    if isinstance(o, dict):
        return {str(k): clean_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean_json(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, dt.datetime, dt.date)):
        return str(o)[:10]
    return o


def key(s):
    k = s.astype(str).str.strip().str.replace(r"\.0+$", "", regex=True)
    return k.mask(s.isna() | k.eq("") | k.str.lower().eq("nan"))


def num(s):
    return pd.to_numeric(s, errors="coerce")


def qtiles(x):
    x = x.dropna()
    if x.empty:
        return {}
    return {f"p{int(q * 100)}": float(x.quantile(q)) for q in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)}


def dstats(x):
    x = x.dropna()
    if x.empty:
        return {"n": 0}
    return {"n": int(len(x)), "min": float(x.min()), "max": float(x.max()), "mean": float(x.mean()),
            "median": float(x.median()), "sum": float(x.sum()), **qtiles(x)}


def load(data_dir, fname, cols):
    target = None
    for p in data_dir.iterdir():
        if p.is_file() and p.name.lower() == fname.lower():
            target = p
    if target is None:
        raise SystemExit(f"ERROR: {fname} not found in {data_dir}")
    for enc in ENCODINGS:
        try:
            hdr = pd.read_csv(target, nrows=0, encoding=enc, dtype=str)
            m = {c.strip().lower(): c for c in hdr.columns}
            missing = [c for c in cols if c not in m]
            if missing:
                raise SystemExit(f"ERROR: {fname} is missing columns {missing}; found {list(hdr.columns)}")
            df = pd.read_csv(target, usecols=[m[c] for c in cols], dtype=str, keep_default_na=False,
                             na_values=[""], encoding=enc)
            df = df.rename(columns={m[c]: c for c in cols})[cols]
            return df, enc, target
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"ERROR: could not decode {fname}")


# --------------------------------------------------------------------------- CHECK 1
def check1(R):
    r = {"calc": ("Group returns.csv by order_item_id; a line is 'multi-return' if it has >1 return row. "
                  "Excess rows = rows beyond the first per line (first = lowest return_id, file order breaks ties)."),
         "grain": "return row (summarised per order line)"}
    r["return_rows"] = int(len(R))
    r["null_order_item_id"] = int(R.oid.isna().sum())
    r["refund_non_numeric"] = int((R.refund_raw.notna() & R.refund.isna()).sum())
    r["refund_null"] = int(R.refund.isna().sum())
    r["refund_zero_or_negative_rows"] = int((R.refund <= 0).sum())
    Rk = R[R.oid.notna()].copy()
    Rk["pos"] = np.arange(len(Rk))
    sk = num(Rk.rid)
    Rk["sk"] = sk if not sk.isna().any() else Rk["pos"]
    Rs = Rk.sort_values(["sk", "pos"], kind="mergesort").copy()
    Rs["occ"] = Rs.groupby("oid").cumcount()
    cnt = Rs.groupby("oid").size()
    multi = cnt[cnt > 1]
    distinct, n_multi = int(len(cnt)), int(len(multi))
    excess = int((cnt - 1).sum())
    r["distinct_returned_lines"] = distinct
    r["multi_return_lines"] = n_multi
    r["excess_rows"] = excess
    r["multiplicity_distribution"] = {int(k): int(v) for k, v in cnt.value_counts().sort_index().items()}
    r["pct_multi_of_returned_lines"] = pc(n_multi, distinct)
    r["pct_excess_of_return_rows"] = pc(excess, len(Rk))
    tot = float(R.refund.sum())
    ism = Rs.oid.isin(multi.index)
    r["refund_total_all_rows"] = tot
    r["refund_on_multi_return_lines_all_rows"] = float(Rs[ism].refund.sum())
    r["refund_on_multi_return_lines_first_row"] = float(Rs[ism & (Rs.occ == 0)].refund.sum())
    r["refund_on_excess_rows"] = float(Rs[Rs.occ > 0].refund.sum())
    r["refund_on_single_return_lines"] = float(Rs[~ism].refund.sum())
    for k in ("refund_on_multi_return_lines_all_rows", "refund_on_excess_rows", "refund_on_single_return_lines"):
        r["pct_of_total_" + k] = pc(r[k], tot)
    if n_multi:
        g = Rs[ism].groupby("oid").refund.nunique()
        r["multi_lines_all_rows_same_refund_value"] = int((g == 1).sum())
        r["multi_lines_rows_with_different_refund_values"] = int((g > 1).sum())
        ml = Rs[ism].groupby("oid").agg(return_rows=("rid", "size"), refund_total=("refund", "sum"),
                                        refund_min=("refund", "min"), refund_max=("refund", "max"))
        r["_multi_df"] = ml.reset_index().rename(columns={"oid": "order_item_id"})
    else:
        r["_multi_df"] = pd.DataFrame(columns=["order_item_id", "return_rows", "refund_total"])
    # devil's advocate: independent route to the excess-row count
    alt_excess = int(len(Rk) - Rk.oid.nunique())
    r["crosscheck_excess_rows_rows_minus_distinct"] = alt_excess
    r["crosscheck_agrees"] = alt_excess == excess
    r["_Rs"] = Rs
    concl = [("Counts and amounts of multi-return lines / excess rows (two independent routes agree)"
              if r["crosscheck_agrees"] else "Excess-row count: routes DISAGREE", OK if r["crosscheck_agrees"] else UNRES)]
    if n_multi:
        concl.append((f"{fi(n_multi)} order lines have more than one return row ({fi(excess)} excess rows). "
                      "Whether they are partial returns, repeats or duplicates is not determinable from the data", ANOM))
        concl.append(("Cause of multiple return rows", UNRES))
    r["conclusions"] = concl
    r["anomaly"] = (f"{fi(n_multi)} multi-return lines; {fi(excess)} excess rows carrying {fm(r['refund_on_excess_rows'], 0)} "
                    f"({fp(r['refund_on_excess_rows'], tot)} of total refund)") if n_multi else "none"
    return r


# --------------------------------------------------------------------------- CHECK 2
def check2(Rs, I):
    r = {"calc": ("line_value = qty x order_items.price. For each returned order_item_id: refund_total = SUM(refund) over its "
                  "return rows; compare refund_total with line_value (excess = refund_total - line_value where > 0)."),
         "grain": "order line (returned lines only)"}
    Iu = I.dropna(subset=["oid"])
    r["order_items_duplicate_ids"] = int(Iu.oid.duplicated().sum())
    Ix = Iu.drop_duplicates("oid").set_index("oid")
    g = Rs.groupby("oid").agg(return_rows=("rid", "size"), refund_total=("refund", "sum"))
    first = Rs[Rs.occ == 0].set_index("oid").refund.rename("refund_first")
    J = g.join(first).join(Ix[["ord", "qty", "price", "line_value"]], how="left")
    r["returned_lines"] = int(len(J))
    r["returned_lines_not_in_order_items"] = int((~g.index.isin(Ix.index)).sum())
    r["returned_lines_with_null_line_value"] = int(J.line_value.isna().sum()) - r["returned_lines_not_in_order_items"]
    M = J.dropna(subset=["line_value"]).copy()
    r["lines_compared"] = int(len(M))
    r["lines_with_line_value_le_0"] = int((M.line_value <= 0).sum())
    M["excess"] = M.refund_total - M.line_value
    ex = M.excess > EPS
    eq = M.excess.abs() <= EPS
    r["lines_refund_gt_line_value"] = int(ex.sum())
    r["lines_refund_eq_line_value"] = int(eq.sum())
    r["lines_refund_lt_line_value"] = int((M.excess < -EPS).sum())
    for k in ("lines_refund_gt_line_value", "lines_refund_eq_line_value", "lines_refund_lt_line_value"):
        r["pct_" + k] = pc(r[k], len(M))
    r["excess_refund_amount"] = float(M.excess[ex].sum())
    r["refund_total_on_exceeding_lines"] = float(M.refund_total[ex].sum())
    r["line_value_of_exceeding_lines"] = float(M.line_value[ex].sum())
    r["total_refund_on_compared_lines"] = float(M.refund_total.sum())
    r["total_line_value_of_compared_lines"] = float(M.line_value.sum())
    r["excess_pct_of_total_refund"] = pc(r["excess_refund_amount"], r["total_refund_on_compared_lines"])
    r["refund_to_line_value_pct_overall"] = pc(r["total_refund_on_compared_lines"], r["total_line_value_of_compared_lines"])
    pos = M[M.line_value > 0]
    r["refund_to_line_value_ratio_stats"] = dstats(pos.refund_total / pos.line_value)
    r["excess_amount_stats_on_exceeding_lines"] = dstats(M.excess[ex])
    exM = M[ex]
    r["exceeding_lines_with_single_return_row"] = int((exM.return_rows == 1).sum())
    r["exceeding_lines_with_multiple_return_rows"] = int((exM.return_rows > 1).sum())
    r["exceeding_lines_first_row_alone_already_exceeds"] = int((exM.refund_first > exM.line_value + EPS).sum())
    r["exceeding_lines_exceed_only_after_adding_later_rows"] = int((exM.refund_first <= exM.line_value + EPS).sum())
    r["multi_row_lines_total"] = int((M.return_rows > 1).sum())
    r["multi_row_lines_exceeding"] = int(((M.return_rows > 1) & ex).sum())
    r["single_row_lines_total"] = int((M.return_rows == 1).sum())
    r["single_row_lines_exceeding"] = int(((M.return_rows == 1) & ex).sum())
    # devil's advocate: row-level merge route
    alt = Rs.merge(Ix[["line_value"]].reset_index(), on="oid", how="inner")
    ag = alt.groupby("oid").agg(rt=("refund", "sum"), lv=("line_value", "first")).dropna(subset=["lv"])
    alt_n = int(((ag.rt - ag.lv) > EPS).sum())
    alt_amt = float((ag.rt - ag.lv)[(ag.rt - ag.lv) > EPS].sum())
    r["crosscheck_exceeding_lines_row_merge_route"] = alt_n
    r["crosscheck_excess_amount_row_merge_route"] = alt_amt
    r["crosscheck_agrees"] = alt_n == r["lines_refund_gt_line_value"] and abs(alt_amt - r["excess_refund_amount"]) < 0.01
    exdf = exM.reset_index().rename(columns={"oid": "order_item_id", "ord": "order_id"})
    r["_exceed_df"] = exdf[["order_item_id", "order_id", "return_rows", "refund_first", "refund_total",
                            "qty", "price", "line_value", "excess"]]
    concl = [("Refund vs line-value comparison (two independent routes agree)"
              if r["crosscheck_agrees"] else "Refund vs line-value: routes DISAGREE", OK if r["crosscheck_agrees"] else UNRES)]
    if r["lines_refund_gt_line_value"]:
        concl.append((f"{fi(r['lines_refund_gt_line_value'])} returned lines carry total refund above qty x price "
                      f"(excess {fm(r['excess_refund_amount'], 0)}). Not assumed to be an error", ANOM))
        concl.append(("Why refund exceeds line value", UNRES))
    r["conclusions"] = concl
    r["anomaly"] = (f"{fi(r['lines_refund_gt_line_value'])} lines "
                    f"({fp(r['lines_refund_gt_line_value'], len(M))} of returned lines); excess {fm(r['excess_refund_amount'], 0)}"
                    if r["lines_refund_gt_line_value"] else "none")
    return r


# --------------------------------------------------------------------------- CHECK 3
def check3(I, P):
    r = {"calc": ("Join order_items.product_id -> products.product_id (many-to-one). diff = order_items.price - products.price. "
                  "Neither column is assumed correct."),
         "grain": "order line"}
    Pk = P.dropna(subset=["pid"])
    r["products_duplicate_ids"] = int(Pk.pid.duplicated().sum())
    Pu = Pk.drop_duplicates("pid")
    Ik = I.dropna(subset=["pid"])
    M = Ik.merge(Pu[["pid", "pprice"]], on="pid", how="left")
    r["order_lines"] = int(len(I))
    r["lines_with_null_product_id"] = int(I.pid.isna().sum())
    r["lines_product_not_found"] = int(M.pprice.isna().sum() - (M.merge(Pu[["pid"]], on="pid", how="inner").shape[0] and 0))
    found = Ik.pid.isin(Pu.pid)
    r["lines_product_not_found"] = int((~found).sum())
    C = M[M.price.notna() & M.pprice.notna()].copy()
    r["lines_compared"] = int(len(C))
    C["diff"] = C.price - C.pprice
    exact = C["diff"].abs() <= EPS
    n, ne = int(len(C)), int(exact.sum())
    r["exact_matches"], r["mismatches"] = ne, n - ne
    r["pct_exact"], r["pct_mismatch"] = pc(ne, n), pc(n - ne, n)
    mm = C[~exact]
    r["diff_all_lines_stats"] = dstats(C["diff"])
    r["diff_mismatches_stats"] = dstats(mm["diff"])
    r["abs_diff_mismatches_stats"] = dstats(mm["diff"].abs())
    r["mismatch_order_items_higher"] = int((mm["diff"] > 0).sum())
    r["mismatch_order_items_lower"] = int((mm["diff"] < 0).sum())
    pp = mm[mm.pprice > 0]
    r["relative_diff_pct_of_products_price_stats"] = dstats(100.0 * pp["diff"] / pp.pprice)
    vc = mm["diff"].round(6).value_counts().head(12)
    r["top_diff_values"] = [{"diff": float(k), "lines": int(v), "pct_of_mismatches": pc(int(v), len(mm))} for k, v in vc.items()]
    r["distinct_diff_values_among_mismatches"] = int(mm["diff"].round(6).nunique())
    bym = C.assign(mm=~exact).groupby("pid").mm.any()
    r["products_with_lines"] = int(len(bym))
    r["products_with_any_mismatch"] = int(bym.sum())
    npx = C.groupby("pid").price.nunique()
    r["products_with_single_order_items_price"] = int((npx == 1).sum())
    r["products_with_multiple_order_items_prices"] = int((npx > 1).sum())
    gi = float((C.qty * C.price).sum())
    gp = float((C.qty * C.pprice).sum())
    r["gross_on_compared_lines_order_items_price"] = gi
    r["gross_on_compared_lines_products_price"] = gp
    r["gross_difference"] = gi - gp
    r["gross_difference_pct_of_order_items_basis"] = pc(gi - gp, gi)
    r["gross_all_lines_order_items_price_anchor"] = float((I.qty * I.price).sum())
    mp = Pu.set_index("pid").pprice
    alt = Ik.pid.map(mp)
    ok = Ik.price.notna() & alt.notna()
    alt_exact = int(((Ik.price - alt).abs()[ok] <= EPS).sum())
    r["crosscheck_exact_matches_map_route"] = alt_exact
    r["crosscheck_agrees"] = alt_exact == ne
    concl = [("Price comparison (two independent routes agree)" if r["crosscheck_agrees"] else "Price comparison: routes DISAGREE",
              OK if r["crosscheck_agrees"] else UNRES)]
    if r["mismatches"]:
        concl.append((f"{fi(r['mismatches'])} lines ({fp(r['mismatches'], n)}) have order_items.price != products.price", ANOM))
        concl.append(("Which price is authoritative, and why they differ", UNRES))
    else:
        concl.append(("order_items.price equals products.price on every compared line", OK))
    r["conclusions"] = concl
    r["anomaly"] = (f"{fi(r['mismatches'])} mismatching lines ({fp(r['mismatches'], n)}); diff min {fm(r['diff_mismatches_stats'].get('min'))}, "
                    f"max {fm(r['diff_mismatches_stats'].get('max'))}") if r["mismatches"] else "none"
    return r


# --------------------------------------------------------------------------- CHECK 4
def check4(O, I, PAY):
    r = {"calc": ("Line-less order = order_id in orders.csv with NO row in order_items.csv. For those orders: "
                  "recorded payment amount per order = SUM(payments.amount) over its payment rows."),
         "grain": "order (line-less orders only)"}
    Ou = O.dropna(subset=["oid"])
    r["orders_duplicate_ids"] = int(Ou.oid.duplicated().sum())
    Ou = Ou.drop_duplicates("oid")
    items_orders = set(I.ord.dropna())
    L = Ou[~Ou.oid.isin(items_orders)]
    r["lineless_orders"] = int(len(L))
    mg = Ou.merge(pd.DataFrame({"oid": list(items_orders)}), on="oid", how="left", indicator=True)
    r["crosscheck_lineless_anti_join_merge"] = int((mg["_merge"] == "left_only").sum())
    r["crosscheck_agrees"] = r["crosscheck_lineless_anti_join_merge"] == r["lineless_orders"]
    pay = PAY.dropna(subset=["ord"]).groupby("ord").agg(pay_rows=("amount", "size"), rec=("amount", "sum"))
    Lx = L.set_index("oid").join(pay, how="left")
    hp = Lx.pay_rows.notna()
    r["lineless_orders_with_payment_rows"] = int(hp.sum())
    r["lineless_orders_without_payment_rows"] = int((~hp).sum())
    r["payment_rows_on_lineless_orders"] = int(Lx.pay_rows.sum()) if hp.any() else 0
    rec = Lx.rec[hp]
    r["recorded_payment_amount_stats"] = dstats(rec)
    r["recorded_payment_amount_total"] = float(rec.sum())
    r["orders_with_recorded_payment_amount_le_0"] = int((rec <= 0).sum())
    tot_all = float(PAY.amount.sum())
    r["recorded_payments_total_all_rows"] = tot_all
    r["pct_of_total_recorded_payments"] = pc(r["recorded_payment_amount_total"], tot_all)
    r["recorded_payments_total_on_all_other_rows"] = tot_all - r["recorded_payment_amount_total"]
    r["payment_amount_null_rows"] = int(PAY.amount.isna().sum())
    concl = [("Line-less order population and recorded payment amount (anti-join routes agree)"
              if r["crosscheck_agrees"] else "Line-less population: routes DISAGREE", OK if r["crosscheck_agrees"] else UNRES)]
    if r["recorded_payment_amount_total"]:
        concl.append((f"{fi(r['lineless_orders_with_payment_rows'])} of {fi(r['lineless_orders'])} line-less orders carry a recorded payment "
                      f"amount (total {fm(r['recorded_payment_amount_total'], 0)}), although they have no order lines", ANOM))
        concl.append(("Meaning of recorded payment amount on orders without lines", UNRES))
    r["conclusions"] = concl
    r["anomaly"] = (f"{fm(r['recorded_payment_amount_total'], 0)} recorded payment amount on {fi(r['lineless_orders_with_payment_rows'])} "
                    f"orders with no order lines ({fp(r['recorded_payment_amount_total'], tot_all)} of all recorded payments)"
                    if r["recorded_payment_amount_total"] else "none")
    return r


# --------------------------------------------------------------------------- CHECK 5
def check5(O):
    r = {"calc": "Parse orders.order_date, keep year = 2024, count orders per calendar date.", "grain": "order (per calendar date)"}
    raw = O.odate_raw
    d = pd.to_datetime(raw, errors="coerce")
    r["order_date_unparsed"] = int((raw.notna() & d.isna()).sum())
    if r["order_date_unparsed"]:
        try:
            d2 = pd.to_datetime(raw, errors="coerce", format="mixed")
            if int((raw.notna() & d2.isna()).sum()) < r["order_date_unparsed"]:
                d = d2
                r["order_date_unparsed"] = int((raw.notna() & d.isna()).sum())
        except Exception:
            pass
    r["order_date_null"] = int(raw.isna().sum())
    r["iso_yyyy_mm_dd_share"] = float(raw.dropna().str.match(r"^\d{4}-\d{2}-\d{2}").mean()) if raw.notna().any() else None
    d0 = d.dt.normalize()
    r["rows_with_time_of_day"] = int((d != d0).sum())
    r["date_min"], r["date_max"] = str(d0.min())[:10], str(d0.max())[:10]
    prior = d0[d0.dt.year < 2024]
    r["latest_date_before_2024"] = str(prior.max())[:10] if len(prior) else None
    m = d0.dt.year == 2024
    daily = d0[m].value_counts().sort_index()
    r["orders_2024_rows"] = int(m.sum())
    r["orders_2024_distinct_order_ids"] = int(O.oid[m].nunique())
    r["distinct_2024_dates"] = int(len(daily))
    r["daily_2024"] = {str(k)[:10]: int(v) for k, v in daily.items()}
    r["all_2024_orders_on_2024_01_01"] = bool(len(daily) == 1 and str(daily.index[0])[:10] == "2024-01-01")
    r["dates_after_2024_01_01"] = int((d0 > pd.Timestamp("2024-01-01")).sum())
    if r["iso_yyyy_mm_dd_share"] is not None and r["iso_yyyy_mm_dd_share"] >= 0.99:
        alt = int(raw.dropna().str.startswith("2024").sum())
        r["crosscheck_string_prefix_2024"] = alt
        r["crosscheck_agrees"] = alt == r["orders_2024_rows"]
    else:
        r["crosscheck_string_prefix_2024"] = None
        r["crosscheck_agrees"] = True
    r["_daily_df"] = pd.DataFrame({"date": [str(k)[:10] for k in daily.index], "orders": daily.values})
    concl = [("2024 order counts per date (string-prefix cross-check agrees)" if r["crosscheck_agrees"]
              else "2024 count: routes DISAGREE", OK if r["crosscheck_agrees"] else UNRES)]
    if r["all_2024_orders_on_2024_01_01"]:
        concl.append((f"All {fi(r['orders_2024_rows'])} orders dated 2024 fall on 2024-01-01 - a single day, not a year", OK))
        concl.append(("2024 must not be treated as a normal full-year period", ANOM))
        concl.append(("Why the data stops at 2024-01-01", UNRES))
    else:
        concl.append((f"2024 orders occur on {fi(r['distinct_2024_dates'])} distinct date(s)", OK))
    r["conclusions"] = concl
    r["anomaly"] = ("all 2024 orders on 2024-01-01 only (partial period)" if r["all_2024_orders_on_2024_01_01"] else "none")
    return r


# --------------------------------------------------------------------------- REPORT
def anchor_rows(c1, c2, c3, c4, c5):
    vals = [("Return rows", PRIOR["return_rows"], c1["return_rows"]),
            ("Returned distinct lines", PRIOR["returned_distinct_lines"], c1["distinct_returned_lines"]),
            ("Refund total (sum of return rows)", PRIOR["refund_total"], c1["refund_total_all_rows"]),
            ("Line-less orders", PRIOR["lineless_orders"], c4["lineless_orders"]),
            ("Recorded payments total", PRIOR["payments_total"], c4["recorded_payments_total_all_rows"]),
            ("Gross sales (qty x order_items.price)", PRIOR["gross_sales"], c3["gross_all_lines_order_items_price_anchor"]),
            ("2024 orders", PRIOR["orders_2024"], c5["orders_2024_distinct_order_ids"])]
    return [(n, p, v, "MATCH" if abs(v - p) <= 0.5 else "DIFFERENT") for n, p, v in vals]


def build_report(c1, c2, c3, c4, c5, integrity, anchors, files_out, data_dir):
    L = []
    w = L.append
    w("# DistribuX targeted follow-up checks (5 checks only)")
    w(f"Run: {dt.datetime.now():%Y-%m-%d %H:%M} | data: `{data_dir}` | pandas {pd.__version__}")
    w(f"Source files unchanged (SHA-256 before vs after): **{'YES' if integrity else 'NO - INVESTIGATE'}**")
    bad = [a for a in anchors if a[3] != "MATCH"]
    w("Anchors vs earlier full audit: " + ("**all MATCH** (" + ", ".join(f"{a[0]}={fi(a[2])}" for a in anchors) + ")"
                                          if not bad else "**DIFFERENT** -> " + "; ".join(f"{a[0]}: was {fi(a[1])}, now {fi(a[2])}" for a in bad)))

    # ---- 1
    w("\n## Check 1 - Refund exposure")
    w(f"Calculation: {c1['calc']}\nGrain: {c1['grain']}")
    w(f"- Return rows: {fi(c1['return_rows'])}; distinct returned lines: {fi(c1['distinct_returned_lines'])}; null order_item_id: {fi(c1['null_order_item_id'])}; non-numeric refund: {fi(c1['refund_non_numeric'])}; refund <= 0 rows: {fi(c1['refund_zero_or_negative_rows'])}")
    w(f"- Lines with >1 return row: **{fi(c1['multi_return_lines'])}** ({fp(c1['multi_return_lines'], c1['distinct_returned_lines'])} of returned lines); excess rows beyond first: **{fi(c1['excess_rows'])}** ({fp(c1['excess_rows'], c1['return_rows'])} of return rows)")
    w(f"- Rows-per-line distribution: {c1['multiplicity_distribution']}")
    t = c1["refund_total_all_rows"]
    w("| Refund amount | Amount | % of total refund |\n|---|--:|--:|")
    w(f"| Total, all return rows | {fm(t)} | 100% |")
    w(f"| All rows on multi-return lines | {fm(c1['refund_on_multi_return_lines_all_rows'])} | {fp(c1['refund_on_multi_return_lines_all_rows'], t)} |")
    w(f"| - of which first row per line | {fm(c1['refund_on_multi_return_lines_first_row'])} | {fp(c1['refund_on_multi_return_lines_first_row'], t)} |")
    w(f"| - of which excess rows (2nd, 3rd...) | {fm(c1['refund_on_excess_rows'])} | {fp(c1['refund_on_excess_rows'], t)} |")
    w(f"| Lines returned once | {fm(c1['refund_on_single_return_lines'])} | {fp(c1['refund_on_single_return_lines'], t)} |")
    if c1["multi_return_lines"]:
        w(f"- Among multi-return lines: all rows carry the same refund value on {fi(c1['multi_lines_all_rows_same_refund_value'])}; rows differ in value on {fi(c1['multi_lines_rows_with_different_refund_values'])} (descriptive only - not a judgement of error)")
    w(f"- Cross-check (rows - distinct lines): {fi(c1['crosscheck_excess_rows_rows_minus_distinct'])} -> {'agrees' if c1['crosscheck_agrees'] else 'DISAGREES'}")
    for txt, lab in c1["conclusions"]:
        w(f"- **{lab}**: {txt}")

    # ---- 2
    w("\n## Check 2 - Refund vs original line value")
    w(f"Calculation: {c2['calc']}\nGrain: {c2['grain']}")
    w(f"- Returned lines: {fi(c2['returned_lines'])}; not found in order_items: {fi(c2['returned_lines_not_in_order_items'])}; null line value: {fi(c2['returned_lines_with_null_line_value'])}; compared: {fi(c2['lines_compared'])}; line value <= 0: {fi(c2['lines_with_line_value_le_0'])}; duplicate order_items ids: {fi(c2['order_items_duplicate_ids'])}")
    w("| Refund total vs line value | Lines | % of compared lines |\n|---|--:|--:|")
    for lab, k in (("Refund > line value", "lines_refund_gt_line_value"), ("Refund = line value", "lines_refund_eq_line_value"), ("Refund < line value", "lines_refund_lt_line_value")):
        w(f"| {lab} | {fi(c2[k])} | {fp(c2[k], c2['lines_compared'])} |")
    w(f"- Excess-refund amount (sum of refund_total - line_value where positive): **{fm(c2['excess_refund_amount'])}** = {fp(c2['excess_refund_amount'], c2['total_refund_on_compared_lines'])} of refund on compared lines ({fm(c2['total_refund_on_compared_lines'])})")
    w(f"- On exceeding lines: refund {fm(c2['refund_total_on_exceeding_lines'])} vs line value {fm(c2['line_value_of_exceeding_lines'])}; overall refund/line-value on all compared lines = {fp(c2['total_refund_on_compared_lines'], c2['total_line_value_of_compared_lines'])}")
    rs = c2["refund_to_line_value_ratio_stats"]
    if rs.get("n"):
        w(f"- Refund/line-value ratio (lines with value>0): min {rs['min']:.3f}, median {rs['median']:.3f}, mean {rs['mean']:.3f}, p95 {rs['p95']:.3f}, max {rs['max']:.3f}")
    if c2["lines_refund_gt_line_value"]:
        w(f"- Exceeding lines by rows: single return row {fi(c2['exceeding_lines_with_single_return_row'])}; multiple rows {fi(c2['exceeding_lines_with_multiple_return_rows'])} (first row alone already exceeds: {fi(c2['exceeding_lines_first_row_alone_already_exceeds'])}; exceed only after later rows added: {fi(c2['exceeding_lines_exceed_only_after_adding_later_rows'])})")
        w(f"- Exceedance rate: single-row lines {fi(c2['single_row_lines_exceeding'])}/{fi(c2['single_row_lines_total'])} ({fp(c2['single_row_lines_exceeding'], c2['single_row_lines_total'])}); multi-row lines {fi(c2['multi_row_lines_exceeding'])}/{fi(c2['multi_row_lines_total'])} ({fp(c2['multi_row_lines_exceeding'], c2['multi_row_lines_total'])})")
    w(f"- Cross-check (row-level merge route): {fi(c2['crosscheck_exceeding_lines_row_merge_route'])} lines, excess {fm(c2['crosscheck_excess_amount_row_merge_route'])} -> {'agrees' if c2['crosscheck_agrees'] else 'DISAGREES'}")
    for txt, lab in c2["conclusions"]:
        w(f"- **{lab}**: {txt}")

    # ---- 3
    w("\n## Check 3 - order_items.price vs products.price")
    w(f"Calculation: {c3['calc']}\nGrain: {c3['grain']}")
    w(f"- Order lines: {fi(c3['order_lines'])}; compared: {fi(c3['lines_compared'])}; product not found: {fi(c3['lines_product_not_found'])}; null product_id: {fi(c3['lines_with_null_product_id'])}; duplicate product ids in products: {fi(c3['products_duplicate_ids'])}")
    w(f"- Exact matches: **{fi(c3['exact_matches'])}** ({fp(c3['exact_matches'], c3['lines_compared'])}); mismatches: **{fi(c3['mismatches'])}** ({fp(c3['mismatches'], c3['lines_compared'])})")
    if c3["mismatches"]:
        s, a = c3["diff_mismatches_stats"], c3["abs_diff_mismatches_stats"]
        w(f"- diff = order_items.price - products.price, mismatches only: min {fm(s['min'])}, max {fm(s['max'])}, mean {fm(s['mean'])}, median {fm(s['median'])}; |diff| mean {fm(a['mean'])}, median {fm(a['median'])}")
        w(f"- Quantiles of diff (mismatches): " + ", ".join(f"{k}={fm(v)}" for k, v in s.items() if k.startswith("p")))
        w(f"- order_items higher: {fi(c3['mismatch_order_items_higher'])}; lower: {fi(c3['mismatch_order_items_lower'])}")
        rd = c3["relative_diff_pct_of_products_price_stats"]
        if rd.get("n"):
            w(f"- Relative diff (% of products.price): min {rd['min']:.2f}, median {rd['median']:.2f}, mean {rd['mean']:.2f}, max {rd['max']:.2f}")
        w(f"- Distinct diff values among mismatches: {fi(c3['distinct_diff_values_among_mismatches'])}. Most frequent: " + "; ".join(f"{x['diff']:+g} ({fi(x['lines'])} lines, {fp(x['lines'], c3['mismatches'], 1)})" for x in c3["top_diff_values"][:8]))
        w(f"- Products with lines: {fi(c3['products_with_lines'])}; with any mismatch: {fi(c3['products_with_any_mismatch'])}; products whose lines share one order_items.price: {fi(c3['products_with_single_order_items_price'])}; with several: {fi(c3['products_with_multiple_order_items_prices'])}")
    w(f"- Sensitivity (compared lines): SUM(qty x order_items.price) = {fm(c3['gross_on_compared_lines_order_items_price'])}; SUM(qty x products.price) = {fm(c3['gross_on_compared_lines_products_price'])}; difference {fm(c3['gross_difference'])} ({fp(c3['gross_difference'], c3['gross_on_compared_lines_order_items_price'], 4)})")
    w(f"- Cross-check (map route) exact matches {fi(c3['crosscheck_exact_matches_map_route'])} -> {'agrees' if c3['crosscheck_agrees'] else 'DISAGREES'}")
    for txt, lab in c3["conclusions"]:
        w(f"- **{lab}**: {txt}")

    # ---- 4
    w("\n## Check 4 - Recorded payment amount on line-less orders")
    w(f"Calculation: {c4['calc']}\nGrain: {c4['grain']}")
    s = c4["recorded_payment_amount_stats"]
    w(f"- Line-less orders: **{fi(c4['lineless_orders'])}** (anti-join cross-check {fi(c4['crosscheck_lineless_anti_join_merge'])} -> {'agrees' if c4['crosscheck_agrees'] else 'DISAGREES'}); with payment rows: {fi(c4['lineless_orders_with_payment_rows'])}; without: {fi(c4['lineless_orders_without_payment_rows'])}; payment rows on them: {fi(c4['payment_rows_on_lineless_orders'])}")
    if s.get("n"):
        w(f"- Recorded payment amount per line-less order: total **{fm(s['sum'])}**, mean {fm(s['mean'])}, median {fm(s['median'])}, min {fm(s['min'])}, max {fm(s['max'])}")
        w(f"- Quantiles: " + ", ".join(f"{k}={fm(v)}" for k, v in s.items() if k.startswith("p")) + f"; orders with amount <= 0: {fi(c4['orders_with_recorded_payment_amount_le_0'])}")
    w(f"- Share of all recorded payments ({fm(c4['recorded_payments_total_all_rows'])}): {fp(c4['recorded_payment_amount_total'], c4['recorded_payments_total_all_rows'])}; all other rows: {fm(c4['recorded_payments_total_on_all_other_rows'])}")
    for txt, lab in c4["conclusions"]:
        w(f"- **{lab}**: {txt}")

    # ---- 5
    w("\n## Check 5 - 2024 daily orders")
    w(f"Calculation: {c5['calc']}\nGrain: {c5['grain']}")
    w(f"- Parsing: unparsed {fi(c5['order_date_unparsed'])}, null {fi(c5['order_date_null'])}, ISO yyyy-mm-dd share {c5['iso_yyyy_mm_dd_share']:.4f}, rows with a time-of-day {fi(c5['rows_with_time_of_day'])}" if c5["iso_yyyy_mm_dd_share"] is not None else "- Parsing: no dates")
    w(f"- Overall date range: {c5['date_min']} to {c5['date_max']}; latest date before 2024: {c5['latest_date_before_2024']}; dates after 2024-01-01: {fi(c5['dates_after_2024_01_01'])}")
    w(f"- 2024 orders: {fi(c5['orders_2024_rows'])} rows / {fi(c5['orders_2024_distinct_order_ids'])} distinct order_ids on {fi(c5['distinct_2024_dates'])} distinct date(s). Cross-check (string prefix '2024'): {c5['crosscheck_string_prefix_2024']} -> {'agrees' if c5['crosscheck_agrees'] else 'DISAGREES'}")
    w("| Date | Orders |\n|---|--:|")
    for k, v in c5["daily_2024"].items():
        w(f"| {k} | {fi(v)} |")
    w(f"- All 2024 orders on 2024-01-01? **{'YES' if c5['all_2024_orders_on_2024_01_01'] else 'NO'}**")
    for txt, lab in c5["conclusions"]:
        w(f"- **{lab}**: {txt}")

    # ---- A
    w("\n## A. Concise results table")
    w("| Check | Key result | Status | Material anomaly |\n|---|---|---|---|")
    st = lambda c: ANOM if c["anomaly"] != "none" else (OK if c["crosscheck_agrees"] else UNRES)
    w(f"| 1 Refund exposure | {fi(c1['multi_return_lines'])} multi-return lines; {fi(c1['excess_rows'])} excess rows; refund on multi-return lines {fm(c1['refund_on_multi_return_lines_all_rows'], 0)} ({fp(c1['refund_on_multi_return_lines_all_rows'], t)}) | {st(c1)} | {c1['anomaly']} |")
    w(f"| 2 Refund vs line value | {fi(c2['lines_refund_gt_line_value'])} of {fi(c2['lines_compared'])} returned lines have refund > qty x price; excess {fm(c2['excess_refund_amount'], 0)} | {st(c2)} | {c2['anomaly']} |")
    w(f"| 3 Price comparison | {fi(c3['exact_matches'])} exact / {fi(c3['mismatches'])} mismatch of {fi(c3['lines_compared'])} ({fp(c3['mismatches'], c3['lines_compared'])} mismatch) | {st(c3)} | {c3['anomaly']} |")
    w(f"| 4 Line-less order payments | {fi(c4['lineless_orders'])} orders; recorded payment amount {fm(c4['recorded_payment_amount_total'], 0)} (mean {fm(s.get('mean'))}, median {fm(s.get('median'))}) | {st(c4)} | {c4['anomaly']} |")
    w(f"| 5 2024 daily orders | {fi(c5['orders_2024_rows'])} orders on {fi(c5['distinct_2024_dates'])} date(s); all on 2024-01-01: {'YES' if c5['all_2024_orders_on_2024_01_01'] else 'NO'} | {st(c5)} | {c5['anomaly']} |")

    # ---- B
    w("\n## B. Material findings")
    n = 0
    if c1["multi_return_lines"]:
        n += 1
        w(f"{n}. {fi(c1['multi_return_lines'])} order lines have multiple return rows; {fi(c1['excess_rows'])} excess rows hold {fm(c1['refund_on_excess_rows'], 0)} ({fp(c1['refund_on_excess_rows'], t)} of total refund). Cause UNRESOLVED.")
    if c2["lines_refund_gt_line_value"]:
        n += 1
        w(f"{n}. {fi(c2['lines_refund_gt_line_value'])} returned lines ({fp(c2['lines_refund_gt_line_value'], c2['lines_compared'])}) have total refund above qty x price; excess {fm(c2['excess_refund_amount'], 0)}. {fi(c2['exceeding_lines_with_single_return_row'])} of them have a single return row. ANOMALY / REQUIRES INTERPRETATION.")
    if c3["mismatches"]:
        n += 1
        w(f"{n}. order_items.price differs from products.price on {fi(c3['mismatches'])} lines ({fp(c3['mismatches'], c3['lines_compared'])}); quantity-weighted, gross would be {fm(c3['gross_on_compared_lines_products_price'], 0)} on products.price vs {fm(c3['gross_on_compared_lines_order_items_price'], 0)} on order_items.price ({fp(c3['gross_difference'], c3['gross_on_compared_lines_order_items_price'], 4)}). Authoritative price UNRESOLVED.")
    if c4["recorded_payment_amount_total"]:
        n += 1
        w(f"{n}. {fi(c4['lineless_orders_with_payment_rows'])} of {fi(c4['lineless_orders'])} line-less orders carry a recorded payment amount totalling {fm(c4['recorded_payment_amount_total'], 0)} ({fp(c4['recorded_payment_amount_total'], c4['recorded_payments_total_all_rows'])} of all recorded payments).")
    if c5["all_2024_orders_on_2024_01_01"]:
        n += 1
        w(f"{n}. All {fi(c5['orders_2024_rows'])} orders dated 2024 fall on 2024-01-01; the data effectively ends at {c5['latest_date_before_2024']} / 2024-01-01.")
    if bad:
        n += 1
        w(f"{n}. Local data differs from the earlier audit on: " + "; ".join(f"{a[0]} ({fi(a[1])} -> {fi(a[2])})" for a in bad) + ".")
    if n == 0:
        w("None: no anomaly detected in the five checks.")

    # ---- C
    w("\n## C. Impact on previous verified metrics")
    gross, paytot = PRIOR["gross_sales"], PRIOR["payments_total"]
    if bad:
        w("- **Anchor mismatch -> YES:** " + "; ".join(f"{a[0]}" for a in bad) + " no longer equal the earlier audit; re-run the full audit before relying on any prior metric.")
    if c1["multi_return_lines"]:
        w(f"- Multi-return rows: changes any verified metric? **NO.** Returned distinct lines ({fi(c1['distinct_returned_lines'])}), line-level and order-level return rates count DISTINCT lines/orders, so extra rows cannot alter them. Refunds ({fm(t, 0)}) is the arithmetic sum of recorded return rows and is unchanged; {fp(c1['refund_on_excess_rows'], t)} of it sits on excess rows.")
    if c2["lines_refund_gt_line_value"]:
        w(f"- Refund > line value: **NO** change in value (Refunds remains the sum of recorded refund rows), but Refunds is not proven to equal the value of returned goods: {fi(c2['lines_refund_gt_line_value'])} lines exceed qty x price by {fm(c2['excess_refund_amount'], 0)} in total.")
    if c3["mismatches"]:
        w(f"- Price mismatch: **NO** change in Gross sales as defined (qty x order_items.price = {fm(c3['gross_all_lines_order_items_price_anchor'], 0)}). It WOULD change if products.price were the intended basis (compared-lines difference {fm(c3['gross_difference'], 0)}, {fp(c3['gross_difference'], c3['gross_on_compared_lines_order_items_price'], 4)}) - which basis is intended is UNRESOLVED.")
    else:
        w("- Price comparison: **NO** change; Gross sales is identical on either price column.")
    if c4["recorded_payment_amount_total"]:
        w(f"- Line-less order payments: **NO** change in Payments total ({fm(c4['recorded_payments_total_all_rows'], 0)}); it INCLUDES {fm(c4['recorded_payment_amount_total'], 0)} on orders with no lines. Orders-with-lines recorded payments = {fm(c4['recorded_payments_total_on_all_other_rows'], 0)}.")
    if c5["all_2024_orders_on_2024_01_01"]:
        w(f"- 2024 daily: **NO** change in '2024 orders = {fi(c5['orders_2024_distinct_order_ids'])}' (verified count), but it is a one-day count, not annual.")
    w("- Unaffected because they do not use these columns: Total orders, Orders with lines, Line-less orders (count), Units, Orders with returns.")

    # ---- D
    w("\n## D. Previous interpretation corrections / qualifications")
    w("1. Wording: 'Gross = qty x LIST price' -> **qty x order_items.price (as recorded)**. " +
      (f"order_items.price differs from products.price on {fp(c3['mismatches'], c3['lines_compared'])} of lines, so it is not shown to be a list price." if c3["mismatches"]
       else "The two columns agree on every compared line, but 'list price' is still undocumented."))
    w("2. Wording: 'Refunds' -> **recorded refund amount (sum of return rows)**; not established as the value of returned goods" +
      (f" ({fi(c2['lines_refund_gt_line_value'])} lines refund more than their line value; {fi(c1['multi_return_lines'])} lines have several return rows)." if (c1["multi_return_lines"] or c2["lines_refund_gt_line_value"]) else "."))
    if c4["recorded_payment_amount_total"]:
        a = paytot / gross * 100
        b = (paytot - c4["recorded_payment_amount_total"]) / gross * 100
        w(f"3. Payments-to-sales comparisons: the earlier {a:.2f}% ratio (recorded payments / gross sales) has a numerator that includes {fm(c4['recorded_payment_amount_total'], 0)} recorded on orders with no lines. Excluding those, recorded payments / gross sales = {b:.2f}%. Both are ratios of recorded amounts only; neither is a collection rate.")
        w(f"4. 'Line-less' means no order_items rows only. {fi(c4['lineless_orders_with_payment_rows'])} of {fi(c4['lineless_orders'])} such orders have a recorded payment amount, so line-less must not be read as unpaid, inactive or cancelled.")
    if c5["all_2024_orders_on_2024_01_01"]:
        w("5. Time series: year-level comparisons must treat 2024 as a single day (2024-01-01) and exclude it from trend/YoY analysis.")

    # ---- E
    w("\n## E. Files / outputs created")
    w("- Script: `distribux_followup_checks.py`")
    for f in files_out:
        w(f"- `{f}`")
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
    data_dir = Path(args[0] if args else DATA_DIR)
    out_dir = Path(out_arg) if out_arg else Path(OUTPUT_DIR)
    if not data_dir.exists():
        raise SystemExit(f"ERROR: folder not found: {data_dir}")
    print(f"DistribuX follow-up checks\n  data: {data_dir}\n  out : {out_dir}\n  (read-only; only the columns needed are loaded)\n")

    raw, paths = {}, {}
    for name, (fname, cols) in FILES.items():
        df, enc, p = load(data_dir, fname, cols)
        raw[name], paths[name] = df, p
        print(f"  loaded {p.name}: {len(df):,} rows ({enc})")
    before = {n: sha256_file(p) for n, p in paths.items()}

    R = pd.DataFrame({"rid": raw["returns"].return_id, "oid": key(raw["returns"].order_item_id),
                      "refund_raw": raw["returns"].refund, "refund": num(raw["returns"].refund)})
    I = pd.DataFrame({"oid": key(raw["items"].order_item_id), "ord": key(raw["items"].order_id),
                      "pid": key(raw["items"].product_id), "qty": num(raw["items"].qty), "price": num(raw["items"].price)})
    I["line_value"] = I.qty * I.price
    P = pd.DataFrame({"pid": key(raw["products"].product_id), "pprice": num(raw["products"].price)})
    O = pd.DataFrame({"oid": key(raw["orders"].order_id), "odate_raw": raw["orders"].order_date.astype(object).where(raw["orders"].order_date.notna(), None)})
    O["odate_raw"] = O.odate_raw.astype(object)
    PAY = pd.DataFrame({"ord": key(raw["payments"].order_id), "amount": num(raw["payments"].amount)})

    c1 = check1(R)
    print("  check 1 done")
    c2 = check2(c1["_Rs"], I)
    print("  check 2 done")
    c3 = check3(I, P)
    print("  check 3 done")
    c4 = check4(O, I, PAY)
    print("  check 4 done")
    c5 = check5(O)
    print("  check 5 done")

    after = {n: sha256_file(p) for n, p in paths.items()}
    integrity = before == after
    anchors = anchor_rows(c1, c2, c3, c4, c5)

    out_dir.mkdir(parents=True, exist_ok=True)
    f_md = out_dir / "followup_checks_report.md"
    f_js = out_dir / "followup_checks_results.json"
    f_d = out_dir / "followup_2024_daily_orders.csv"
    f_m = out_dir / "followup_multi_return_lines.csv"
    f_x = out_dir / "followup_refund_exceeds_line_value.csv"
    c5["_daily_df"].to_csv(f_d, index=False, encoding="utf-8-sig")
    c1["_multi_df"].to_csv(f_m, index=False, encoding="utf-8-sig")
    c2["_exceed_df"].to_csv(f_x, index=False, encoding="utf-8-sig")
    files_out = [str(f.resolve()) for f in (f_md, f_js, f_d, f_m, f_x)]
    report = build_report(c1, c2, c3, c4, c5, integrity, anchors, files_out, data_dir)
    f_md.write_text(report, encoding="utf-8")
    res = {"source_unchanged": integrity, "sha256_before": before, "anchors": anchors}
    for n, c in (("check1_refund_exposure", c1), ("check2_refund_vs_line_value", c2), ("check3_price_comparison", c3),
                 ("check4_lineless_order_payments", c4), ("check5_2024_daily_orders", c5)):
        res[n] = {k: v for k, v in c.items() if not k.startswith("_")}
    f_js.write_text(json.dumps(clean_json(res), indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n" + report)
    print(f"Done. Report: {f_md}\nPaste followup_checks_report.md back into Claude.")


if __name__ == "__main__":
    main()
