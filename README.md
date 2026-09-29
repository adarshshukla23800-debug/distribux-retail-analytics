# DistribuX — Retail Distribution Intelligence & Decision Support System

*"From Transaction Data to Actionable Business Decisions."*

An MBA-level Business Intelligence / Business Analytics project demonstrating the full pipeline: **Data → Information → Insight → Decision → Action**, built on a synthetic 12-table retail distribution dataset.

## Overview

DistribuX audits, validates, and analyzes a 12-table transactional retail dataset (300,000 orders, 600,000 order lines, 1.5M units, ~3.83B in recorded sales) to produce defensible business metrics, insights, visualizations, and a decision-support dashboard — without overstating what the data can support.

## Dataset disclosure

**This dataset is synthetic.** It is used to demonstrate analytical methodology and decision-support process, not to represent a real company, a real retailer, or real market conditions. No claim in this project should be read as describing an actual business.

## Business problem & objectives

A retail distributor needs to understand its sales performance across stores, products, categories, and time, and to know honestly which questions its transaction data can — and cannot — answer, before building any dashboard or making any recommendation.

Objectives:
1. Validate the raw data before calculating anything from it.
2. Calculate only metrics the data can actually support, with grain and join logic stated explicitly.
3. Distinguish real patterns from statistical noise before calling anything an "insight."
4. Present results without inflating recorded figures into unsupported business claims (revenue, profit, cash collected, etc.).

## Data architecture

12 CSV tables: `orders`, `order_items`, `payments`, `returns`, `products`, `categories`, `suppliers`, `stores`, `employees`, `customers`, `promotions`, `shipments`. Full key relationships, cardinalities, and orphan-rate checks are documented in the forensic audit output (`audit_output/audit_report.md`).

## Methodology

Every stage uses a controlled, evidence-gated loop:

```
ANALYZE → CHALLENGE → DEVIL'S ADVOCATE → CORRECT (if needed) → REVALIDATE
        → CHECK AGAINST PROJECT GOALS → FINAL GATE → NEXT STAGE
```

No stage is skipped; no stage is repeated once its gate passes. Full-project stages:

| Stage | What it does | Script |
|---|---|---|
| 0 | Forensic data audit (keys, duplicates, orphans, fan-out) | `distribuX_local_audit.py` |
| 0.5 | Targeted follow-up checks on specific anomalies | `distribuX_followup_checks.py` || 1 | Business Analytics Blueprint (this repo's KPI spec) | — |
| 2 | P1 core metrics | `distribux_p1_metrics.py` |
| 3 | Statistical insight analysis (concentration, trend, outliers) | `distribux_p1_insight_analysis.py` |
| 4 | P2 targeted metrics (category, supplier, return-rate-by-group, customer spend) | `distribux_p2_metrics.py` |
| 5 | Business insight layer | `DISTRIBUX_INSIGHTS.md` |
| 6 | Visualizations | `distribux_visualizations.py` |
| 7 | Dashboard | `distribux_dashboard.py` |

## Key metrics (KPI dictionary, abridged)

| Metric | Formula | Result | Status |
|---|---:|---:|---|
| Recorded Gross Sales | SUM(qty × order_items.price) | 3,827,746,136 | VERIFIED |
| Orders with order lines | COUNT(DISTINCT order_id) | 259,233 | VERIFIED |
| Units | SUM(qty) | 1,500,518 | VERIFIED |
| Line-Level Return Rate | distinct returned lines / distinct lines | 4.8752% | VERIFIED |
| Recorded Payments | SUM(payments.amount) | 3,018,080,810 | VERIFIED (diagnostic only vs. sales) |

**Terminology rules enforced throughout:** "Recorded Gross Sales," never "Revenue." "Recorded Payment Amount," never "Cash Received" or "Collection Rate." order_items.price is the working sales basis; products.price is not assumed authoritative (99.977% of lines disagree between the two).

## Key insights (see `DISTRIBUX_INSIGHTS.md` for full detail)

- **Sales are almost perfectly uniform** across stores (top store = 1.05%), products (top = 0.02%), categories (top = 3.57%), suppliers (top = 0.69%), and customers (top = 0.008%) — consistent with synthetic, near-random data generation, not real retail skew.
- **The 2020–2023 trend is flat** (annual range under 1%; R² = 0.047), not growing or declining.
- **Return rate is stable** across stores (3.98–5.65%) and categories (4.52–5.26%), centered tightly on the 4.88% overall rate.
- **Payments and line-item sales do not reconcile** at the order level (only 28 of 259,233 orders match within ±1) — a diagnostic finding, not a KPI.
- **731 lines have multiple return rows; 7,498 lines refund more than their own line value** — refund totals are real but not equivalent to "value of goods returned."

## What this project deliberately does NOT calculate

Profit, margin, COGS, inventory turnover, delivery time, employee performance, promotion ROI, cash collection rate, customer acquisition cost, current market conditions, or customer signup cohorts (66.93% of customers have a signup date after their first order). The dataset does not contain the fields these require.

## Project structure

```
DistribuX/
├── data/                        12 source CSVs (read-only)
├── docs/                        00-10: dataset manifest through forensic audit log
├── audit_output/
│   ├── audit_report.md          Stage 0 forensic audit
│   ├── followup_checks_report.md
│   ├── p1_metrics/               Stage 2 outputs
│   ├── p1_insight_analysis/      Stage 3 outputs
│   └── p2_metrics/               Stage 4 outputs
├── analysis/                    Copies of validated outputs for reference
├── visualizations/               Stage 6 PNG charts
├── dashboard/
│   └── distribux_dashboard.py   Stage 7 Streamlit app
├── scripts/
│   ├── distribuX_local_audit.py
│   ├── distribuX_followup_checks.py
│   ├── distribux_p1_metrics.py
│   ├── distribux_p1_insight_analysis.py
│   ├── distribux_p2_metrics.py
│   └── distribux_visualizations.py
├── reports/
│   ├── DISTRIBUX_INSIGHTS.md     Stage 5 business insight layer
│   └── DISTRIBUX_FINAL_REPORT.md
├── README.md
└── requirements.txt
```

## Setup & how to run

```bash
pip install -r requirements.txt

# Stage 2 - core metrics (run once against your real data/ folder)
python scripts/distribux_p1_metrics.py

# Stage 3 - statistical insight analysis
python scripts/distribux_p1_insight_analysis.py

# Stage 4 - targeted secondary metrics
python scripts/distribux_p2_metrics.py

# Stage 6 - charts
python scripts/distribux_visualizations.py

# Stage 7 - dashboard
streamlit run dashboard/distribux_dashboard.py
```

Each script defaults to the paths under `C:\Users\...\DistribuX\`; pass a folder path and `--out` to override.

## Reproducibility

Every script is read-only against the source CSVs (SHA-256 hash-verified before/after every run) and every metric states its exact formula, grain, join, and source columns in its output report. No number in the dashboard or report is calculated inline — everything traces back to a Stage 2/3/4 CSV.

## Limitations

- Dataset is synthetic; findings demonstrate methodology, not real-world business conditions.
- `order_items.price` vs `products.price` disagreement (99.977% of lines) is unresolved; sales figures use `order_items.price` throughout.
- Payments and refunds are recorded amounts, not confirmed cash received or confirmed goods value.
- 2024 contains only 195 orders, all dated 2024-01-01, and is excluded from trend analysis.
- Return rate is row-count based, not value-weighted.

## Future improvements

- Resolve the `order_items.price` vs `products.price` discrepancy with source-system documentation.
- Add a documented payment-status field to make a real collection-rate metric possible.
- Investigate the root cause of multi-return lines and over-value refunds.
