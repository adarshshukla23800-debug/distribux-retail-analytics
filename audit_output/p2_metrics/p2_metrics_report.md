# DistribuX P2 metrics report (targeted, Stage 4)
Run: 2026-09-27 05:31 | data: `/mnt/user-data/uploads` | pandas 3.0.2
Source files unchanged: **YES**

## CHALLENGE
- None: product_id is a unique key; all order_items rows join cleanly to products and orders.

## Metrics
| Metric | Formula | Grain | Table(s) | Join | Result | Status |
|---|---|---|---|---|--:|---|
| P2-1 Recorded Sales by Category | SUM(qty x price) GROUP BY products.category_id | order line -> product -> category | order_items, products | order_items -> products (M:1) | 3,827,746,136.00 | **VERIFIED** |
| P2-2 Recorded Sales by Supplier | SUM(qty x price) GROUP BY products.supplier_id | order line -> product -> supplier | order_items, products | order_items -> products (M:1), reused from P2-1 | 3,827,746,136.00 | **VERIFIED** |
| P2-3 Line-Level Return Rate by Store/Category/Product | COUNT(DISTINCT returned order_item_id) / COUNT(DISTINCT order_item_id), grouped | order line, grouped by store / category / product | order_items, returns, orders, products | order_items -> orders (store); order_items -> products (category) - each a separate M:1 join, never combined | 4.8752% | **VERIFIED** |
| P2-4 Recorded Spend per Customer | SUM(qty x price) per order, summed per customer | order line -> order -> customer | order_items, orders | order_items -> orders (M:1), reused pattern from P1-2 | 3,827,746,136.00 | **VERIFIED** |

## Notes and cross-checks
**P2-1 Recorded Sales by Category**
- Sum across categories = 3,827,746,136.00; expected (lines with a valid product_id) = 3,827,746,136.00 -> identity holds. Concentration: top category = 3.5714%, HHI = 334.1 (unconcentrated (HHI < 1,500)), Gini = 0.027.
**P2-2 Recorded Sales by Supplier**
- Sum across suppliers = 3,827,746,136.00; expected = 3,827,746,136.00 -> identity holds. Concentration: top supplier = 0.6885%, HHI = 51.0 (unconcentrated (HHI < 1,500)), Gini = 0.081.
**P2-3 Line-Level Return Rate by Store/Category/Product**
- Overall: 29,251/600,000 = 4.8752% (matches Stage-2 anchor 29,251/600,000: yes). Cross-checks: distinct returned lines summed across products = 29,251 (matches overall numerator 29,251); across categories = 29,251 (matches); across stores (lines with a resolvable order) = 29,251 (matches the store-joined numerator 29,251). Store return rate range (stores with >=30 lines, to avoid small-sample noise): 3.9804% to 5.6537%. Category return rate range: 4.5195% to 5.2611%. This answers the Stage-3 'NOT ANSWERABLE FROM P1' gap: return rates are compared across groups here for the first time.
**P2-4 Recorded Spend per Customer**
- Sum across customers = 3,827,746,136.00; expected (lines whose order was found) = 3,827,746,136.00 -> identity holds. Excludes line-less orders by construction (same as P1-2), not labelled unpaid/cancelled. Concentration: top customer = 0.0083%, HHI = 0.3 (unconcentrated (HHI < 1,500)), Gini = 0.300. Orders per customer: min 1, median 5, max 17.

## Business-readable summary
- **Category concentration**: top category = 3.5714% of sales, HHI = 334 (unconcentrated (HHI < 1,500)) -> same near-uniform pattern seen in P1 store/product concentration, now confirmed at category grain too.
- **Supplier concentration**: top supplier = 0.6885% of sales, HHI = 51 (unconcentrated (HHI < 1,500)).
- **Return rate distribution** (the Stage-3 gap): overall 4.8752% (29,251/600,000, matches the Stage-2 anchor). Across stores with a meaningful sample (>=30 lines), the rate ranges 3.9804% to 5.6537%; across categories, 4.5195% to 5.2611%. This range is narrow relative to the overall rate, consistent with the same near-uniform pattern seen elsewhere - REQUIRES INTERPRETATION before concluding any store/category is a genuine outlier.
- **Customer spend concentration**: top customer = 0.0083% of recorded spend, HHI = 0 (unconcentrated (HHI < 1,500)); median orders per customer = 5.

## Goal check
All four P2 metrics reuse the same M:1 join discipline as P1, no 1:N child table is combined with another in one computation, and P2-3 closes the exact gap Stage 3 flagged as unanswerable. No correction was necessary; final gate PASSES.

## Final gate
PASS

## Terminology
- Recorded Gross Sales, not Revenue. Recorded spend, not confirmed cash receipt.
- No profit, margin, COGS, inventory turnover, delivery time, employee performance, or promotion ROI claim is made.

## Files produced
- p2_sales_by_category.csv
- p2_sales_by_supplier.csv
- p2_return_rate_by_product.csv
- p2_return_rate_by_category.csv
- p2_return_rate_by_store.csv
- p2_spend_by_customer.csv
- p2_metrics_report.md (this file)
