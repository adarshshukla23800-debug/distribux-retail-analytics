#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
distribux_dashboard.py
========================
Stage 7: Presentation layer only. Reads the validated Stage 2/4 CSV
outputs - it does not recompute any metric, join any raw table, or
contain business logic that isn't already in a validated script.
If a number here looks wrong, the bug is in Stage 2/4, not here.

USAGE:
    streamlit run distribux_dashboard.py

Expects, relative to AUDIT_OUTPUT_DIR:
    p1_metrics\\p1_total_recorded_gross_sales.csv
    p1_metrics\\p1_sales_by_store.csv
    p1_metrics\\p1_orders_units_by_store.csv
    p1_metrics\\p1_sales_by_product.csv
    p1_metrics\\p1_sales_trend_monthly.csv / _quarterly.csv / _annual.csv
    p1_metrics\\p1_line_return_rate.csv
    p2_metrics\\p2_sales_by_category.csv
    p2_metrics\\p2_sales_by_supplier.csv
    p2_metrics\\p2_return_rate_by_store.csv
    p2_metrics\\p2_return_rate_by_category.csv
    p2_metrics\\p2_spend_by_customer.csv

Requires: streamlit, pandas.
"""
from pathlib import Path

import pandas as pd
import streamlit as st

# Default: the audit_output folder next to this project (dashboard/ is one level below the project root),
# so it works wherever the DistribuX folder is placed. Editable in the sidebar.
AUDIT_OUTPUT_DIR = str(Path(__file__).resolve().parent.parent / "audit_output")

st.set_page_config(page_title="DistribuX — Retail Distribution Intelligence", layout="wide")


@st.cache_data
def load(base, subdir, fname):
    p = Path(base) / subdir / fname
    if not p.exists():
        st.error(f"Missing file: {p}\n\nRun distribux_p1_metrics.py / distribux_p2_metrics.py first.")
        st.stop()
    return pd.read_csv(p, encoding="utf-8-sig")


def money(x):
    return f"{x:,.0f}"


base = st.sidebar.text_input("audit_output folder", AUDIT_OUTPUT_DIR)
st.sidebar.caption("Change this if your project folder is in a different location.")
st.sidebar.markdown("---")
st.sidebar.markdown(
    "**Dataset note:** synthetic dataset, used to demonstrate analytical methodology, "
    "not real market data. Recorded Gross Sales = qty × order_items.price (products.price "
    "authority unresolved). Payments are not treated as cash received. 2024 is a partial "
    "period (195 orders, all on 2024-01-01) and is excluded from the trend."
)

total = load(base, "p1_metrics", "p1_total_recorded_gross_sales.csv")
store_sales = load(base, "p1_metrics", "p1_sales_by_store.csv")
store_ou = load(base, "p1_metrics", "p1_orders_units_by_store.csv")
product_sales = load(base, "p1_metrics", "p1_sales_by_product.csv")
monthly = load(base, "p1_metrics", "p1_sales_trend_monthly.csv")
quarterly = load(base, "p1_metrics", "p1_sales_trend_quarterly.csv")
annual = load(base, "p1_metrics", "p1_sales_trend_annual.csv")
return_rate = load(base, "p1_metrics", "p1_line_return_rate.csv")
category_sales = load(base, "p2_metrics", "p2_sales_by_category.csv")
supplier_sales = load(base, "p2_metrics", "p2_sales_by_supplier.csv")
return_by_store = load(base, "p2_metrics", "p2_return_rate_by_store.csv")
return_by_category = load(base, "p2_metrics", "p2_return_rate_by_category.csv")
spend_by_customer = load(base, "p2_metrics", "p2_spend_by_customer.csv")

st.title("DistribuX — Retail Distribution Intelligence & Decision Support System")
st.caption("From Transaction Data to Actionable Business Decisions.")

# ---- TOP KPI AREA ----
total_sales = float(total["recorded_gross_sales"].iloc[0])
total_orders = int(store_ou["orders"].sum())
total_units = int(store_ou["units"].sum())
rr = return_rate.iloc[0]
k1, k2, k3, k4 = st.columns(4)
k1.metric("Recorded Gross Sales", money(total_sales))
k2.metric("Orders (with lines)", f"{total_orders:,}")
k3.metric("Units", f"{total_units:,}")
k4.metric("Line-Level Return Rate", f"{rr['line_level_return_rate_pct']:.2f}%")
st.caption("Recorded Gross Sales = SUM(qty × order_items.price). Return rate = distinct returned lines ÷ distinct order lines.")

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["Sales Trend", "Store Performance", "Product & Category", "Returns", "Diagnostics"])

# ---- TAB 1: TREND ----
with tab1:
    st.subheader("Recorded Gross Sales Trend, 2020–2023")
    st.caption("2024 is excluded here: all 195 orders fall on 2024-01-01, a single day, not a full period.")
    grain = st.radio("Granularity", ["Monthly", "Quarterly", "Annual"], horizontal=True, index=1)
    df = {"Monthly": monthly, "Quarterly": quarterly, "Annual": annual}[grain]
    xcol = {"Monthly": "month", "Quarterly": "quarter", "Annual": "year"}[grain]
    st.line_chart(df.set_index(xcol)["recorded_gross_sales"])
    cv = df["recorded_gross_sales"].std() / df["recorded_gross_sales"].mean()
    st.caption(f"Coefficient of variation across {grain.lower()} periods: {cv:.4f} — "
              f"{'a flat trend' if cv < 0.05 else 'meaningful period-to-period variation'}, "
              "not a forecast.")

# ---- TAB 2: STORE ----
with tab2:
    st.subheader("Recorded Sales by Store")
    merged = store_sales.merge(store_ou, on="store_id", how="left").sort_values(
        "recorded_gross_sales", ascending=False)
    top_share = 100 * merged["recorded_gross_sales"].iloc[0] / merged["recorded_gross_sales"].sum()
    st.caption(f"{len(merged)} stores. Top store = {top_share:.2f}% of total sales — "
              "sales are close to evenly split across stores; treat any single store's rank as "
              "descriptive, not as evidence of an operational cause.")
    st.bar_chart(merged.set_index("store_id")["recorded_gross_sales"])
    st.dataframe(merged, use_container_width=True, hide_index=True)

# ---- TAB 3: PRODUCT & CATEGORY ----
with tab3:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Top 15 Products")
        top_p = product_sales.sort_values("recorded_gross_sales", ascending=False).head(15)
        st.bar_chart(top_p.set_index("product_id")["recorded_gross_sales"])
        st.caption("Based on order_items.price. products.price is not used (authority unresolved).")
    with c2:
        st.subheader("Recorded Sales by Category")
        cat_sorted = category_sales.sort_values("recorded_gross_sales", ascending=False)
        st.bar_chart(cat_sorted.set_index("category_id")["recorded_gross_sales"])
    st.subheader("Recorded Sales by Supplier")
    sup_sorted = supplier_sales.sort_values("recorded_gross_sales", ascending=False)
    st.bar_chart(sup_sorted.set_index("supplier_id")["recorded_gross_sales"])
    st.caption(f"{len(sup_sorted)} suppliers; top supplier = "
              f"{100*sup_sorted['recorded_gross_sales'].iloc[0]/sup_sorted['recorded_gross_sales'].sum():.2f}% of total sales.")

# ---- TAB 4: RETURNS ----
with tab4:
    overall_rate = rr["line_level_return_rate_pct"]
    st.subheader(f"Overall Line-Level Return Rate: {overall_rate:.4f}%")
    st.caption(f"{int(rr['numerator_distinct_returned_lines']):,} distinct returned lines / "
              f"{int(rr['denominator_distinct_order_lines']):,} distinct order lines. "
              "Row-count based; not weighted by units or refund value.")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**By store** (stores with ≥30 lines)")
        rbs = return_by_store[return_by_store["lines"] >= 30].sort_values("return_rate_pct", ascending=False)
        st.bar_chart(rbs.set_index("store_id")["return_rate_pct"])
        st.caption(f"Range: {rbs['return_rate_pct'].min():.2f}% – {rbs['return_rate_pct'].max():.2f}%. "
                  "A narrow, roughly bell-shaped spread around the overall rate — consistent with sampling "
                  "noise, not a store-level outlier.")
    with c2:
        st.markdown("**By category**")
        rbc = return_by_category.sort_values("return_rate_pct", ascending=False)
        st.bar_chart(rbc.set_index("category_id")["return_rate_pct"])
        st.caption(f"Range: {rbc['return_rate_pct'].min():.2f}% – {rbc['return_rate_pct'].max():.2f}%.")

# ---- TAB 5: DIAGNOSTICS ----
with tab5:
    st.subheader("Data-quality and reconciliation diagnostics")
    st.markdown(
        "These are **diagnostic findings**, established in the forensic audit and follow-up checks stages "
        "on this same dataset — not KPIs to trend or optimize."
    )
    st.markdown(
        """
- **Payment vs. sales reconciliation:** of 259,233 orders with order lines, only 28 have a recorded
  payment amount matching their line-item sales value (±1). 61.1% pay less, 38.9% pay more.
  Total recorded payments (3,018,080,810) and total Recorded Gross Sales (3,827,746,136) are on
  different bases and are **not** a "collection rate."
- **Line-less orders:** 40,767 orders have no order_items row, yet still carry a recorded payment
  amount. Not labelled cancelled, unpaid, or inactive — that has not been established.
- **Multi-return lines:** 731 order lines have more than one return row.
- **Refund exceeding line value:** 7,498 returned lines have a total recorded refund greater than
  their own recorded line value (qty × price). The refund total is real and verifiable, but is
  **not** equivalent to "value of goods returned" without this caveat.
- **Price ambiguity:** order_items.price and products.price disagree on effectively all lines
  (99.977%). This dashboard uses order_items.price throughout; products.price authority remains
  unresolved.
- **Customer spend concentration:** top customer accounts for only 0.008% of recorded spend
  (HHI ≈ 0.3) — consistent with the near-uniform pattern seen at every other grain.
        """
    )
    st.subheader("Customer spend distribution (top 20)")
    st.bar_chart(spend_by_customer.sort_values("total_spend", ascending=False).head(20)
                .set_index("customer_id")["total_spend"])

st.markdown("---")
st.caption(
    "DistribuX uses a synthetic dataset to demonstrate BI/analytics methodology, not to represent a "
    "real retailer or real market conditions. Unsupported metrics (profit, margin, COGS, inventory "
    "turnover, delivery time, employee performance, promotion ROI, cash collection rate, and customer "
    "signup cohorts) are intentionally excluded — the underlying data does not support them."
)