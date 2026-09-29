# DistribuX Stage 3 - P1 results insight analysis
Run: 2026-09-27 05:27 | source: `/home/claude/p1_real_out` | pandas 3.0.2
This reads the Stage-2 P1 output CSVs only. It does not touch the raw 12-table dataset and does not recompute any P1 metric. All findings below are statistical descriptions with a named method; none is a business recommendation.

## 1. Store sales concentration (from p1_sales_by_store.csv)
Calculation: share of Recorded Gross Sales per store_id; HHI = sum(share%^2) on a 0-10,000 scale (US DOJ/FTC merger-guideline bands used only as a reference point); Gini coefficient (0=equal, 1=maximally concentrated); Pareto point = minimum stores reaching 80% of total sales.
- Stores with sales: 100; total 3,827,746,136.00
- Top store (9): 1.05% of total sales
- Top 3 stores: 3.14%; top 5 stores: 5.22%
- HHI = 100.1 -> unconcentrated (HHI < 1,500)
- Gini coefficient = 0.014
- Pareto point: 80 store(s) (80.00% of all stores) account for 80.73% of total sales
- **DESCRIPTIVE PATTERN**: this describes how sales are distributed across stores. It does NOT say why any store is higher or lower (store size, region, product mix, data coverage are all untested explanations) - **REQUIRES INTERPRETATION** before any resourcing conclusion is drawn.

## 2. Product sales concentration (from p1_sales_by_product.csv)
Calculation: identical method to section 1, applied to product_id.
- Products with sales: 10,000; total 3,827,746,136.00
- Top product (745): 0.02% of total sales
- Top 3 products: 0.05%; top 5 products: 0.08%
- HHI = 1.0 -> unconcentrated (HHI < 1,500)
- Gini coefficient = 0.090
- Pareto point: 7,506 product(s) (75.06% of all products) account for 80.01% of total sales
- **DESCRIPTIVE PATTERN**: a small-N Pareto point (classic 80/20 pattern) is common in retail sales data by construction and is not, by itself, evidence of a demand insight - **REQUIRES INTERPRETATION**.

## 3. Orders/units vs sales consistency by store (from p1_orders_units_by_store.csv)
Calculation: for each store, does a high sales share also mean a high order/unit share, or does the store differ mainly in average order value? This distinguishes 'more orders' from 'bigger orders' as possible explanations for a sales difference - it does not resolve which one is the cause.
- Correlation (sales share vs order-count share across stores): r = 0.848
- Average recorded sales per order, by store: min 14,247.69 (88), max 15,257.64 (38)
- **REQUIRES INTERPRETATION**: Sales share tracks order-count share closely (stores differ mainly by how many orders they take)

## 4. Monthly trend (2020-2023)
Calculation: growth% = period-over-period % change in Recorded Gross Sales; coefficient of variation (CV) = std/mean across periods; linear trend = ordinary least-squares slope of sales against period index (a descriptive line-of-best-fit, not a forecast); outliers = Tukey's IQR rule (< Q1-1.5*IQR or > Q3+1.5*IQR). Best for spotting short outlier periods; noisiest granularity.
- Periods: 48 (2020-01 to 2023-12)
- Lowest: 2022-02 = 72,023,974.00; Highest: 2020-12 = 83,415,848.00
- Coefficient of variation: 0.0335
- Linear trend slope: 15,183.32 per period, R^2 = 0.006 (a straight line explains only part of the movement - the series is not simply monotonic)
- Period-over-period growth range: -10.40% to 13.53%
- Outlier period(s) (Tukey's rule, fence 72,959,398.50 to 87,349,376.50): 2021-02 = 72,469,043.00; 2022-02 = 72,023,974.00
- **REQUIRES INTERPRETATION**: an outlier period is a statistical fact, not an explained one; do not attribute it to a business cause not evidenced elsewhere in the dataset.
- **CORRELATION-VS-CAUSATION CAUTION**: this trend is SUM(qty x order_items.price) over time. P1 does not track order count, active store count, or pricing changes over time separately, so a rising or falling total could reflect more orders, more active stores, or price changes - not necessarily 'growing demand.' Decomposing this is a P2 candidate, not a P1 conclusion.

## 5. Quarterly trend (2020-2023)
Calculation: growth% = period-over-period % change in Recorded Gross Sales; coefficient of variation (CV) = std/mean across periods; linear trend = ordinary least-squares slope of sales against period index (a descriptive line-of-best-fit, not a forecast); outliers = Tukey's IQR rule (< Q1-1.5*IQR or > Q3+1.5*IQR). Smooths monthly noise; a reasonable balance for a report chart.
- Periods: 16 (2020-Q1 to 2023-Q4)
- Lowest: 2022-Q1 = 233,992,079.00; Highest: 2022-Q4 = 245,110,491.00
- Coefficient of variation: 0.0129
- Linear trend slope: 140,784.69 per period, R^2 = 0.047 (a straight line explains only part of the movement - the series is not simply monotonic)
- Period-over-period growth range: -3.52% to 1.90%
- No statistical outlier periods found by Tukey's rule.
- **CORRELATION-VS-CAUSATION CAUTION**: this trend is SUM(qty x order_items.price) over time. P1 does not track order count, active store count, or pricing changes over time separately, so a rising or falling total could reflect more orders, more active stores, or price changes - not necessarily 'growing demand.' Decomposing this is a P2 candidate, not a P1 conclusion.

## 6. Annual trend (2020-2023)
Calculation: growth% = period-over-period % change in Recorded Gross Sales; coefficient of variation (CV) = std/mean across periods; linear trend = ordinary least-squares slope of sales against period index (a descriptive line-of-best-fit, not a forecast); outliers = Tukey's IQR rule (< Q1-1.5*IQR or > Q3+1.5*IQR). Coarsest view; year-over-year growth only, 4 data points.
- Periods: 4 (2020 to 2023)
- Lowest: 2021 = 950,027,068.00; Highest: 2020 = 959,634,059.00
- Coefficient of variation: 0.0045
- Linear trend slope: 247,810.80 per period, R^2 = 0.006 (a straight line explains only part of the movement - the series is not simply monotonic)
- Period-over-period growth range: -1.00% to 0.81%
- Outlier period(s) (Tukey's rule, fence 951,968,593.75 to 962,152,349.75): 2021 = 950,027,068.00
- **REQUIRES INTERPRETATION**: an outlier period is a statistical fact, not an explained one; do not attribute it to a business cause not evidenced elsewhere in the dataset.
- **CORRELATION-VS-CAUSATION CAUTION**: this trend is SUM(qty x order_items.price) over time. P1 does not track order count, active store count, or pricing changes over time separately, so a rising or falling total could reflect more orders, more active stores, or price changes - not necessarily 'growing demand.' Decomposing this is a P2 candidate, not a P1 conclusion.

## 7. Return rate distribution
Calculation: attempted a distribution question (is the return rate concentrated by store/product/category?) against p1_line_return_rate.csv.
- P1-6 provides a single **overall** figure: 29,251 / 600,000 = 4.88%. No store, product or category breakdown exists in the P1 outputs.
- **NOT ANSWERABLE FROM P1**: whether returns are concentrated or broadly distributed requires a return-rate-by-store/category/product metric, which is a named P2 candidate in the Business Analytics Blueprint, not a P1 metric. This script does not estimate or approximate that breakdown.

## Classification: real pattern vs data artifact
| # | Question | Finding | Status |
|---|---|---|---|
| 1 | Is store sales concentrated? | Top store = 1.05%, HHI = 100 (unconcentrated (HHI < 1,500)) | **DESCRIPTIVE PATTERN** |
| 2 | Are a few products dominating sales? | Top 7506 products = 80.01% of sales | **DESCRIPTIVE PATTERN** |
| 3 | Are sales growing consistently 2020-2023? | Slope 247,810.80, R^2 0.006 | **REQUIRES INTERPRETATION** |
| 4 | Are there unusual periods? | 2021-02, 2022-02 | **REQUIRES INTERPRETATION** |
| 5 | Are return rates concentrated or broadly distributed? | single overall figure only, no breakdown | **NOT ANSWERABLE FROM P1** |
| 6 | Correlation vs causation risk on the sales trend? | present on every trend finding above | **REQUIRES INTERPRETATION** |

## Goal check
This script answers the Stage-3 questions using named, standard statistical methods only (share-of-total, HHI, Gini, Pareto point, Tukey IQR outliers, OLS trend slope/R^2). No finding above is stated as a business cause; every concentration and trend finding is explicitly marked as requiring interpretation before it becomes an insight, and the return-rate distribution question is marked not answerable from P1 rather than estimated.

## Final gate
PASS - proceed to a scoped decision on which REQUIRES INTERPRETATION findings are worth carrying into Stage 4 (P2), and whether return-rate-by-store/category/product is worth adding as a P2 metric to answer question 5 above. Do not move to Stage 5 (business insight layer) or Stage 6 (visualization) until that decision is made.
