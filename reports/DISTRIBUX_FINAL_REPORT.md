# DistribuX — Final Project Report

**Retail Distribution Intelligence & Decision Support System**
*"From Transaction Data to Actionable Business Decisions."*

---

## 1. Executive Summary

DistribuX is a business analytics project built on a synthetic 12-table retail distribution dataset (300,000 orders; 1.5 million units; ~3.83 billion in recorded sales). The project's purpose is not to produce the largest number of charts or the most sophisticated technique, but to demonstrate a defensible analytical pipeline: every metric traces to a stated formula, grain, and join path; every insight is checked against alternative explanations before being called an insight; and every unsupported metric (profit, margin, inventory turnover, delivery time, employee performance, promotion ROI) is explicitly excluded rather than approximated.

The central finding of the entire project is that **sales, returns, and customer spend are all close to uniformly distributed** across every dimension tested — stores, products, categories, suppliers, and customers. This is disclosed as a likely property of the synthetic data generation process, not presented as a real business insight to act on.

## 2. Business Problem

A retail distributor operating across 100 stores, 10,000 products, and 30 categories needs to know, from its transaction data alone: where sales concentrate, whether performance is trending, where returns cluster, and whether its payment records reconcile with its sales records — while being explicit about which of these questions the data can actually answer.

## 3. Project Objectives

1. Validate the raw dataset (keys, duplicates, orphans, fan-out risk) before calculating anything.
2. Calculate a minimal, well-defined set of core metrics (P1) directly from validated tables.
3. Test those metrics statistically before treating any pattern as a business insight (P1 insight analysis).
4. Extend only to secondary metrics (P2) that either reveal structure at a different grain or close a specific gap identified in the insight analysis.
5. Convert validated findings into insight statements using an Observation → Evidence → Interpretation → Limitation → Business Implication → Possible Action structure.
6. Visualize only what answers a stated business question, with no misleading axes.
7. Present everything in a lightweight dashboard that adds no new business logic beyond what the scripts already validated.

## 4. Dataset Overview

12 CSV tables: `orders`, `order_items`, `payments`, `returns`, `products`, `categories`, `suppliers`, `stores`, `employees`, `customers`, `promotions`, `shipments`. Core scale: 300,000 orders, 600,000 order-item rows, 30,000 return rows, 100 stores, 10,000 products, 30 categories, 200 suppliers, 49,751 distinct customers with at least one order line.

**Synthetic data disclosure:** this dataset was not confirmed to originate from a real business. Its near-perfectly uniform distribution across every grain tested (see Section 9) is itself evidence consistent with synthetic, close-to-random generation. All findings in this report describe the dataset's properties and demonstrate the analytical process; none should be read as describing a real retailer.

## 5. Data Architecture

`orders` is the central fact table, referenced by `order_items` (many-to-one), `payments` (one row per order in this dataset), and indirectly by `returns` (via `order_items`). `order_items` references `products`, which references `categories` and `suppliers`. `customers` and `stores` attach to `orders`. `employees` reference `stores` but have no link to any transactional table, which is why employee performance is unsupported (Section 10).

## 6. Data Quality / Validation (Stage 0)

The forensic audit (run before this report, on the same real dataset) established: 0 duplicate rows in tested tables, validated primary keys on all 12 tables, and validated foreign-key relationships with the following material findings:
- 259,233 of 300,000 orders have at least one order line; 40,767 do not, yet many still carry a recorded payment.
- Joining `orders → order_items → payments` naively (without pre-aggregating each child separately) inflates totals by roughly 2.14× — this fan-out risk governs every join in every downstream script.
- `order_items.price` and `products.price` disagree on 99.977% of lines; price authority is unresolved and `order_items.price` is used as the working basis throughout.
- 2024 contains only 195 orders, all dated 2024-01-01 — a single day, not a representative period.
- 731 order lines have more than one return row; 7,498 returned lines have a total refund exceeding their own recorded line value.

## 7. Analytical Methodology

Every stage used the evidence-gated loop: **Analyze → Challenge → Devil's Advocate → Correct (if needed) → Revalidate → Check against goals → Final gate**, applied conditionally — a metric that passed its challenge was closed, not re-litigated; only a metric with a genuine discrepancy triggered targeted revalidation. All scripts are read-only against the source CSVs, verified by SHA-256 hash comparison before and after every run.

## 8. KPI Definitions

| KPI | Formula | Grain | Status |
|---|---|---|---|
| Recorded Gross Sales | SUM(qty × order_items.price) | order line | VERIFIED |
| Recorded Sales by Store/Product/Category/Supplier | same, grouped | order line → group | VERIFIED |
| Orders and Units by Store | COUNT(DISTINCT order_id), SUM(qty), grouped | order/line → store | VERIFIED |
| Recorded Sales Trend | same, grouped by month/quarter/year, 2020-2023 only | order line → period | VERIFIED |
| Line-Level Return Rate (overall and by group) | COUNT(DISTINCT returned order_item_id) / COUNT(DISTINCT order_item_id) | order line (grouped) | VERIFIED |
| Recorded Spend per Customer | SUM(qty × price) per order, summed per customer | order line → order → customer | VERIFIED |

Full formulas, join paths, and fan-out safeguards are documented in each stage's `.md` report under `audit_output/`.

## 9. Sales Analysis

Total Recorded Gross Sales: **3,827,746,136**. This reconciles exactly: the 2020–2023 trend total (3,825,268,676) plus the 2024 single-day total (2,477,460) equals the full total with zero residual. The quarterly trend has a coefficient of variation of 1.3% and a linear-trend R² of 0.047 — the series is flat, not growing or declining, over the four-year window.

## 10. Store Analysis

100 stores. Top store = 1.05% of total sales; HHI = 100.1, indistinguishable from a perfectly even 100-way split (which would itself produce HHI=100). Store sales share correlates strongly with order-count share (r=0.848) — stores differ mainly by how many orders they take, not by dramatically different average order values (range: 14,247.69–15,257.64 per order). **This is a descriptive pattern requiring interpretation, not a resourcing recommendation** — no operational cause (region, staffing, footfall) is tested by this data.

## 11. Product / Category Analysis

10,000 products: top product = 0.02% of sales; HHI ≈ 1. 30 categories: top category = 3.57% of sales; HHI = 334.1 (still classified unconcentrated on the standard HHI scale). 200 suppliers: top supplier = 0.69%; HHI = 51.0. All four levels show the same near-uniform pattern as store sales.

## 12. Trend Analysis

Annual sales ranged only from 950,027,068 (2021) to 959,634,059 (2020) — about a 1% spread. Two months (2021-02, 2022-02) and one year (2021) were flagged as statistical outliers by Tukey's IQR rule, but the margin is small relative to the overall level. **Caution:** Recorded Gross Sales cannot distinguish "more orders" from "different pricing" as a cause of any period-to-period change — this decomposition was not attempted and no growth/decline claim is made.

## 13. Return Analysis

Overall line-level return rate: **4.8752%** (29,251 of 600,000 lines). Store-level rates range 3.98%–5.65%; category-level rates range 4.52%–5.26%. The store-level distribution is symmetric and centered on the overall rate — the pattern expected from sampling noise around a single true rate, not from any genuine high- or low-return outlier. Separately (data-quality diagnostics, not return-rate KPIs): 731 lines have multiple return rows, and 7,498 lines have a total refund exceeding their own line value — both are disclosed as unexplained anomalies, not attributed to any cause.

## 14. Additional Validated Diagnostics

- **Payment-vs-sales reconciliation:** only 28 of 259,233 orders-with-lines have a recorded payment matching their line value within ±1; 61.1% pay less, 38.9% pay more. This is a diagnostic finding about the dataset's internal consistency, not a KPI.
- **Line-less order payments:** 40,767 orders with no order lines still carry a recorded payment amount, worth a material share of total recorded payments. Not labelled cancelled, unpaid, or inactive.
- **Customer spend:** 49,751 customers; top customer = 0.008% of spend; median 5 orders per customer.

## 15. Business Insights

See `reports/DISTRIBUX_INSIGHTS.md` for the full Observation → Evidence → Interpretation → Limitation → Business Implication → Possible Action write-up of all five insights summarized in Sections 9–14 above.

## 16. Decision Support

Given the near-uniform distribution found at every grain, this dataset in its current form does not support a defensible store-investment, product-assortment, or customer-targeting recommendation — any such recommendation would be fitting a narrative to statistical noise. The one area with genuine decision value is **data-quality/process investigation**: the payment-vs-sales gap, the line-less-order payments, the multi-return lines, and the over-value refunds are all real, reproducible anomalies that would warrant investigation in a real operational system, independent of whether the sales figures themselves show any pattern.

## 17. Limitations

- Dataset is synthetic; disclosed as such throughout.
- Price authority (`order_items.price` vs `products.price`) is unresolved.
- Payments are recorded amounts, not confirmed cash receipts.
- Refund totals are not confirmed to equal the value of goods returned.
- Return rate is row-count based, not value-weighted.
- 2024 is a single-day partial period, excluded from trend analysis.
- No cost, inventory, delivery-date, or employee-attribution data exists, so profit, margin, inventory turnover, delivery time, and employee performance are not calculated anywhere in this project.

## 18. Conclusion

DistribuX demonstrates a complete, gated BI pipeline from raw CSVs to a decision-support dashboard, with every number reproducible from a stated formula and every claim checked against what the data can actually support. The project's most defensible output is not a growth chart or a top-seller list — both would be reporting noise as if it were signal — but the disciplined process of testing for concentration and trend before claiming either, and the explicit refusal to manufacture metrics (profit, ROI, delivery time) the data cannot support.

## 19. Technical Implementation

Python 3.8+, pandas, numpy, matplotlib, Streamlit. Six scripts, each independently runnable, each read-only against the source CSVs, each producing a markdown report documenting its own formulas, joins, and validation checks. See `README.md` for the full script list and run order.

## 20. Reproducibility

Every script hash-verifies its source files unchanged before and after execution. Every metric's report states its exact SQL-equivalent formula, grain, join path, and any correction applied during validation. Re-running any script against the same `data/` folder reproduces the same output byte-for-byte (excluding timestamps).
