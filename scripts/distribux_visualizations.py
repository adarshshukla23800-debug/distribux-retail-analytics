#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
distribux_visualizations.py
============================
Stage 6: Visualizations. Read-only - reads Stage 2/4 output CSVs only,
does not touch the raw 12 CSVs and does not recompute any metric.

Generates exactly the charts that answer a stated business question from
the Business Analytics Blueprint - no chart is included "because the data
allows it." Each chart states its source metric and its known limitation
directly in the title/caption, consistent with the terminology rules
(Recorded Gross Sales, not Revenue; 2024 excluded/shown separately).

Charts produced (all PNG, business-readable, no 3D, no misleading axes):
  01_sales_trend_quarterly.png      - quarterly trend, 2020-2023 (+ 2024 marked separately)
  02_store_sales_distribution.png   - sorted store sales (shows the near-uniform spread)
  03_top10_products.png             - top 10 products by recorded sales
  04_category_sales.png             - recorded sales by category
  05_return_rate_by_store.png       - distribution of store-level return rates
  06_return_rate_by_category.png    - category-level return rates vs overall line

USAGE:
    python distribux_visualizations.py
    python distribux_visualizations.py "C:\\...\\audit_output" --out "C:\\...\\visualizations"

Expects P1 outputs in <source>\\p1_metrics\\ and P2 outputs in <source>\\p2_metrics\\.
Requires Python 3.8+, pandas, matplotlib.
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

AUDIT_OUTPUT_DIR = r"C:\Users\adars\OneDrive\Desktop\DistribuX\audit_output"
OUTPUT_DIR = r"C:\Users\adars\OneDrive\Desktop\DistribuX\visualizations"

plt.rcParams.update({"figure.dpi": 130, "font.size": 10, "axes.titlesize": 12,
                     "axes.titleweight": "bold", "axes.spines.top": False, "axes.spines.right": False})


def money_fmt(x, pos):
    if x >= 1e9:
        return f"{x/1e9:.1f}B"
    if x >= 1e6:
        return f"{x/1e6:.0f}M"
    if x >= 1e3:
        return f"{x/1e3:.0f}K"
    return f"{x:.0f}"


def load(p1, p2, fname, subdir):
    path = (p1 if subdir == "p1" else p2) / fname
    if not path.exists():
        raise SystemExit(f"ERROR: {fname} not found in {path.parent}. Run the Stage 2/4 metrics scripts first.")
    return pd.read_csv(path, encoding="utf-8-sig")


def main():
    args = sys.argv[1:]
    out_arg = None
    if "--out" in args:
        i = args.index("--out")
        out_arg = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    base = Path(args[0] if args else AUDIT_OUTPUT_DIR)
    out_dir = Path(out_arg) if out_arg else Path(OUTPUT_DIR)
    p1, p2 = base / "p1_metrics", base / "p2_metrics"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Reading P1 from: {p1}\nReading P2 from: {p2}\nWriting charts to: {out_dir}\n")

    # ---- 01: quarterly trend, 2020-2023, with 2024 called out ----
    q = load(p1, p2, "p1_sales_trend_quarterly.csv", "p1")
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(q["quarter"], q["recorded_gross_sales"], marker="o", color="#2563eb", linewidth=2)
    ax.set_title("Recorded Gross Sales by Quarter, 2020-2023")
    ax.set_ylabel("Recorded Gross Sales (qty x order_items.price)")
    ax.set_ylim(bottom=0)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(money_fmt))
    ax.tick_params(axis="x", rotation=45)
    ax.set_xlabel("Quarter\n(2024 excluded: all 195 orders fall on 2024-01-01, not a full period. Y-axis starts at 0;\n"
                  "quarter-to-quarter variation is small, CV=1.3% - a flat trend, not a strong seasonal pattern.)")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "01_sales_trend_quarterly.png")
    plt.close(fig)

    # ---- 02: store sales distribution (sorted, shows the near-uniform spread) ----
    s = load(p1, p2, "p1_sales_by_store.csv", "p1").sort_values("recorded_gross_sales", ascending=False).reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(range(len(s)), s["recorded_gross_sales"], color="#2563eb", width=0.9)
    ax.axhline(s["recorded_gross_sales"].mean(), color="#dc2626", linestyle="--", linewidth=1.2,
              label=f"Mean = {s['recorded_gross_sales'].mean()/1e6:.1f}M")
    ax.set_title(f"Recorded Gross Sales by Store, Ranked ({len(s)} stores)")
    ax.set_ylabel("Recorded Gross Sales")
    ax.set_xlabel("Store rank (highest to lowest) - near-uniform spread; top store = "
                  f"{100*s['recorded_gross_sales'].iloc[0]/s['recorded_gross_sales'].sum():.2f}% of total")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(money_fmt))
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "02_store_sales_distribution.png")
    plt.close(fig)

    # ---- 03: top 10 products ----
    pr = load(p1, p2, "p1_sales_by_product.csv", "p1").sort_values("recorded_gross_sales", ascending=False).head(10)
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.barh(pr["product_id"].astype(str)[::-1], pr["recorded_gross_sales"][::-1], color="#059669")
    ax.set_title("Top 10 Products by Recorded Gross Sales")
    ax.set_xlabel("Recorded Gross Sales (order_items.price basis - products.price authority unresolved)")
    ax.set_ylabel("Product ID")
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(money_fmt))
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "03_top10_products.png")
    plt.close(fig)

    # ---- 04: category sales ----
    c = load(p1, p2, "p2_sales_by_category.csv", "p2").sort_values("recorded_gross_sales", ascending=False)
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(c["category_id"].astype(str), c["recorded_gross_sales"], color="#7c3aed")
    ax.set_title(f"Recorded Gross Sales by Category ({len(c)} categories)")
    ax.set_ylabel("Recorded Gross Sales")
    ax.set_xlabel("Category ID")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(money_fmt))
    ax.tick_params(axis="x", rotation=90, labelsize=7)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "04_category_sales.png")
    plt.close(fig)

    # ---- 05: return rate by store (histogram) ----
    rs = load(p1, p2, "p2_return_rate_by_store.csv", "p2")
    rs = rs[rs["lines"] >= 30]
    overall = 4.8752
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.hist(rs["return_rate_pct"], bins=20, color="#f59e0b", edgecolor="white")
    ax.axvline(overall, color="#dc2626", linestyle="--", linewidth=1.5, label=f"Overall rate = {overall}%")
    ax.set_title("Line-Level Return Rate by Store (stores with >=30 lines)")
    ax.set_xlabel("Return rate (%)  -  distinct returned lines / distinct lines, per store")
    ax.set_ylabel("Number of stores")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "05_return_rate_by_store.png")
    plt.close(fig)

    # ---- 06: return rate by category ----
    rc = load(p1, p2, "p2_return_rate_by_category.csv", "p2").sort_values("return_rate_pct", ascending=False)
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(rc["category_id"].astype(str), rc["return_rate_pct"], color="#f59e0b")
    ax.axhline(overall, color="#dc2626", linestyle="--", linewidth=1.2, label=f"Overall rate = {overall}%")
    ax.set_title("Line-Level Return Rate by Category")
    ax.set_ylabel("Return rate (%)")
    ax.set_xlabel("Category ID")
    ax.tick_params(axis="x", rotation=90, labelsize=7)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "06_return_rate_by_category.png")
    plt.close(fig)

    print("Charts written:")
    for f in sorted(out_dir.glob("*.png")):
        print(f"  - {f.name}")


if __name__ == "__main__":
    main()
