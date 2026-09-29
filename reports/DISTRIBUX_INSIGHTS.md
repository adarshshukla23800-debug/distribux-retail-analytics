# DistribuX — Business Insights (Stage 5)

Format used for every insight: **Observation → Evidence → Interpretation → Limitation → Business Implication → Possible Action.**
All figures below come from the validated Stage 2 (P1) and Stage 4 (P2) outputs, run against the real 12-table dataset. No number here is invented or estimated.

---

## Insight 1 — Sales are almost perfectly uniform across stores, products, categories, suppliers, and customers

**OBSERVATION:** At every grain tested, the top contributor's share of Recorded Gross Sales is far below what any real retail distribution typically shows: top store 1.05% (of 100 stores), top product 0.02% (of 10,000), top category 3.57% (of 30), top supplier 0.69% (of 200), top customer 0.008% (of 49,751).

**EVIDENCE:** `p1_sales_by_store.csv`, `p1_sales_by_product.csv`, `p2_sales_by_category.csv`, `p2_sales_by_supplier.csv`, `p2_spend_by_customer.csv`. HHI at every level is under 350 (a scale that runs to 10,000); a genuinely even 100-store split would itself produce an HHI of exactly 100 — the actual store HHI (100.1) is indistinguishable from that baseline.

**INTERPRETATION:** This pattern is consistent with the dataset being synthetically generated with close-to-uniform random assignment across stores, products, and customers, rather than reflecting the skew (a small number of best-sellers, flagship stores, or high-value customers) that real transaction data almost always shows.

**LIMITATION:** This cannot be independently confirmed as synthetic — that determination was already disclosed as a dataset characteristic before this analysis began. What this analysis adds is that the uniformity is present at every grain tested, not just one.

**BUSINESS IMPLICATION:** Any store-ranking, product-ranking, or customer-segmentation exercise built on this dataset should not be presented as if it reflects real market behavior. The differences that do exist (e.g., store 9 at 1.05% vs. store 88 at ~0.83%) are more consistent with random noise than with an operational cause.

**POSSIBLE ACTION:** None — this is a data-characteristic disclosure, not a decision-triggering finding. It should be stated plainly in the final report's limitations section rather than used to recommend closing, resourcing, or promoting anything.

---

## Insight 2 — The 2020–2023 sales trend is flat, not growing or declining

**OBSERVATION:** Annual Recorded Gross Sales ranged only from 950.0M (2021) to 959.6M (2020) — a spread of about 1%. The quarterly linear trend slope has an R² of 0.047 out of a possible 1.0, meaning a straight line explains under 5% of the quarter-to-quarter movement.

**EVIDENCE:** `p1_sales_trend_annual.csv`, `p1_sales_trend_quarterly.csv`, and the Stage 3 insight-analysis report's linear-trend calculation.

**INTERPRETATION:** There is no meaningful growth or decline over the four-year window. Two months (2021-02, 2022-02) and one year (2021) were flagged as statistical outliers by Tukey's rule, but the margin is small (2021's total is within 1% of the other years).

**LIMITATION:** Recorded Gross Sales = SUM(qty × order_items.price). This dataset does not separately track order count or active-store count over time, so a flat total could theoretically mask an order-count increase offset by a price decrease, or vice versa — that decomposition was not attempted here, per the Stage 3 correlation-vs-causation caution.

**BUSINESS IMPLICATION:** No seasonal or year-over-year planning conclusion should be drawn from this trend — "flat" is itself the finding, not a signal to look for.

**POSSIBLE ACTION:** None warranted from this metric alone.

---

## Insight 3 — Line-level return rate is stable across stores and categories, with no genuine outlier

**OBSERVATION:** Overall return rate is 4.8752% (29,251 of 600,000 lines). Across the 100 stores, the rate ranges only 3.98%–5.65%; across 30 categories, only 4.52%–5.26%. The store-level distribution (see chart 05) is a symmetric, roughly bell-shaped spread centered on the overall rate — the signature of statistical noise around a single population rate, not of a genuine high- or low-return outlier.

**EVIDENCE:** `p2_return_rate_by_store.csv`, `p2_return_rate_by_category.csv`; cross-checked so that distinct returned lines summed across every store/category/product grouping equal the same overall numerator (29,251) in each case.

**INTERPRETATION:** No store or category shows a return rate that stands out as an operational problem; the small variation seen is what would be expected from binomial sampling noise on the same underlying ~4.9% rate.

**LIMITATION:** This is a row-count-based rate — it does not weight by units or by refund value, and it does not distinguish full returns from partial returns.

**BUSINESS IMPLICATION:** There is no evidence here to target any specific store or category for return-reduction investigation based on rate alone.

**POSSIBLE ACTION:** None triggered by this finding specifically.

---

## Insight 4 — Recorded payments and recorded line-item sales do not reconcile at the order level (established in the earlier forensic phase, restated here for completeness)

**OBSERVATION:** Of the 259,233 orders with order lines, only 28 have a recorded payment amount matching their line-item sales value within ±1. 61.1% pay less, 38.9% pay more than their line value. Separately, 40,767 orders have no order lines but still carry a recorded payment amount.

**EVIDENCE:** Established in the Stage 0 forensic audit and the targeted follow-up checks (both run on this same real dataset before Stage 2). Total recorded payments = 3,018,080,810 vs. total Recorded Gross Sales = 3,827,746,136 — different bases, not directly comparable as a "collection rate."

**INTERPRETATION:** Payments and line-item sales appear to be two independently generated fields in this dataset rather than a reconciled pair (e.g., payment = sales − discount − adjustment).

**LIMITATION:** This is a diagnostic finding, not a KPI. Do not compute or present a "collection rate" or "payment coverage %" from these two totals — that ratio implies a relationship the data doesn't support.

**BUSINESS IMPLICATION:** In a real operational system, this gap would warrant investigating the payment-capture and order-fulfillment pipelines separately, since payment and sales records don't currently correspond one-to-one.

**POSSIBLE ACTION:** Flag as a process/data-quality item for further investigation; do not build a dashboard KPI on the payment-vs-sales ratio.

---

## Insight 5 — Return and refund records contain internal anomalies that limit their use as monetary figures

**OBSERVATION:** 731 order lines have more than one return row, and 7,498 returned lines have a total recorded refund that exceeds their own recorded line value (qty × price).

**EVIDENCE:** Established in the targeted follow-up checks on this same real dataset.

**INTERPRETATION:** The refund total (75,962,300) is a real, verifiable sum of recorded return rows, but it cannot be equated with "value of goods returned" without qualification, since a meaningful share of lines refund more than their own line value.

**LIMITATION:** No business rule in the data explains either anomaly (e.g., restocking fees, penalty refunds, or data entry duplication are all untested hypotheses).

**BUSINESS IMPLICATION:** Any monetary returns-value metric must be labeled "recorded refund amount," never "value of returned goods," and should carry this caveat wherever shown.

**POSSIBLE ACTION:** Flag for data-quality investigation; do not build a "returned goods value" KPI without this qualification attached.

---

## What was deliberately NOT turned into an insight

Per the Business Analytics Blueprint's Section D, the following were evaluated and rejected as unsupported, and no insight above relies on them: profit/margin/COGS, inventory turnover, delivery time, employee performance, promotion ROI, cash collection rate, customer acquisition cost, current market conditions, customer signup cohorts (signup date postdates first order for 66.93% of customers), and "clean" returned-goods value. None of these appear anywhere in this document or in the dashboard/report that follow.
