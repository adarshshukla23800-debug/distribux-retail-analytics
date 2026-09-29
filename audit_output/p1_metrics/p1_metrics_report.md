# DistribuX P1 metrics report
Run: 2026-09-27 05:27 | data: `/mnt/user-data/uploads` | pandas 3.0.2
Source files unchanged (SHA-256 before vs after, order_items/orders/returns only): **YES**

## CHALLENGE - data-quality checks relevant to these 6 metrics
- None: order_item_id and order_id are unique keys; qty, price, store_id and order_date are clean for the rows these metrics use. No correction was necessary.

## Metrics
| Metric | Formula | Grain | Table(s) | Join | Result | Status |
|---|---|---|---|---|--:|---|
| P1-1 Recorded Gross Sales | SUM(order_items.qty x order_items.price) | order line | order_items | No join | 3,827,746,136.00 | **VERIFIED** |
| P1-2 Recorded Sales by Store | SUM(qty x price) GROUP BY orders.store_id | order line -> store | order_items, orders | order_items -> orders (M:1) | 3,827,746,136.00 | **VERIFIED** |
| P1-3 Orders and Units by Store | COUNT(DISTINCT order_id), SUM(qty) GROUP BY orders.store_id | order -> store (orders); order line -> store (units) | order_items, orders | order_items -> orders (M:1), reused from P1-2 | see CSV | **VERIFIED** |
| P1-4 Recorded Sales by Product | SUM(qty x price) GROUP BY order_items.product_id | order line -> product | order_items | No join | 3,827,746,136.00 | **VERIFIED** |
| P1-5 Recorded Sales Trend 2020-2023 | SUM(qty x price) GROUP BY month/quarter/year of orders.order_date | order line -> order -> calendar period | order_items, orders | order_items -> orders (M:1) | 3,825,268,676.00 | **VERIFIED** |
| P1-6 Overall Line-Level Return Rate | COUNT(DISTINCT returned order_item_id) / COUNT(DISTINCT order_item_id) | order line | returns, order_items | returns -> order_items (existence check, not a value join) | 0.05 | **VERIFIED** |

## Notes, cross-checks and corrections (evidence-gated loop)
**P1-1 Recorded Gross Sales**
- 'Recorded' because order_items.price is used, not products.price (authority unresolved). Anchor check vs prior full-audit figure 3,827,746,136: MATCH (diff 0.00).
- Correction applied: none required
**P1-2 Recorded Sales by Store**
- Excludes the 0 orphan-order lines (if any) and ALL 40,767-scale line-less orders by construction, since they have no order_items row to attribute to a store. Sum across stores = 3,827,746,136.00; expected = 3,827,746,136.00 -> identity holds.
- Correction applied: none required
**P1-3 Orders and Units by Store**
- Orders and units are grouped from the SAME order_items->orders join as P1-2 (no re-join). Distinct orders across store buckets = 259,233; distinct orders in the join = 259,233 -> identity holds. Units sum matches SUM(qty) on the same join.
- Correction applied: none required
**P1-4 Recorded Sales by Product**
- Uses order_items.price only; products.price is NOT substituted (authority unresolved). Sum across products = 3,827,746,136.00 vs P1-1 total 3,827,746,136.00 -> identity holds.
- Correction applied: none required
**P1-5 Recorded Sales Trend 2020-2023**
- Filtered to 2020-01-01..2023-12-31 inclusive. Excluded: 390 lines after 2023-12-31 (2024's 195 single-day orders), 0 before 2020-01-01, 0 with an unparseable/missing date, plus the 0 orphan-order lines already excluded in the CHALLENGE step. Monthly/quarterly/annual sums reconcile to the same filtered total (3,825,268,676.00): yes. Years present: [2020, 2021, 2022, 2023] -> 2024 correctly excluded.
- Correction applied: none required
**P1-6 Overall Line-Level Return Rate**
- Numerator = COUNT(DISTINCT order_item_id) in returns.csv that also exist in order_items.csv = 29,251 (NOT COUNT(*) of return rows, which would include the 731 multi-return lines' extra rows). 0 return rows reference an order_item_id not found in order_items.csv and are excluded from the numerator. Denominator = distinct order_item_id in order_items.csv = 600,000 (equals row count, i.e. the key is unique). Numerator vs prior full-audit anchor 29,251: MATCH.
- Correction applied: none required

## Goal check (Data -> Information -> Insight -> Decision -> Action)
All six P1 metrics use a single-table or single M:1-join path, none introduce a second 1:N child table, every cross-check reconciles, and each result carries the qualification needed to defend it in an MBA viva (recorded vs confirmed, line-less orders excluded by construction, 2024 excluded from the trend). No correction was necessary beyond routing nulls to an 'UNKNOWN' bucket; final gate PASSES.

## Final gate
PASS

## Terminology used
- **Recorded Gross Sales** (not "Revenue"): SUM(order_items.qty x order_items.price); price authority unresolved.
- Line-less orders (orders with no order_items row) are excluded from P1-2/P1-3/P1-5 by construction, not labelled cancelled, unpaid or inactive.
- No profit, margin, COGS, inventory turnover, delivery time, employee performance, promotion ROI, or cash-collection claim is made anywhere in this script.

## Files produced
- p1_total_recorded_gross_sales.csv
- p1_recorded_gross_sales_by_year_all_years.csv (all years present, for reference; 2024 shown separately, not merged into the P1-5 trend)
- p1_sales_by_store.csv
- p1_orders_units_by_store.csv
- p1_sales_by_product.csv
- p1_sales_trend_monthly.csv
- p1_sales_trend_quarterly.csv
- p1_sales_trend_annual.csv
- p1_line_return_rate.csv
- p1_metrics_report.md (this file)
