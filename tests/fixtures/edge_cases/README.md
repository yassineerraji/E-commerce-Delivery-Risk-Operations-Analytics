# Edge-case fixtures (synthetic, hand-written)

Tiny Olist-shaped files used by the integration and temporal tests. Every row is invented;
the values exist only to exercise one rule each. Expected results are asserted in
`tests/integration` and `tests/temporal`.

| Order | Case | Expected behaviour |
|---|---|---|
| o01 | Seller `s_a` history, late (delivered 01-15 12:00, promised 01-10) | `is_late = true`, `delay_days = 5`; usable in histories after 2018-01-15 12:00 |
| o02 | Delivered **on the promised day** at 15:00 (promise stored at midnight); row duplicated **identically** | `is_late = false`, `delay_days = 0`; one `strict_duplicate` logged, one row kept |
| o03 | Purchased before t(o05) but **delivered after** t(o05) | absent from o05's history |
| o04 | Delivered **exactly at** t(o05) = 2018-01-20 10:00:00 | excluded from o05's history (strict `<`) |
| o05 | Target order for `s_a`, approved 2018-01-20 10:00:00; payment 100.00 vs items+freight 95.00 | history: 2 known outcomes, late rate 0.5, mean positive delay 2.5, 4 approvals in 30 days; value gap reported, not forced |
| o06 | **Canceled**; approved at the **same second** as o05 | scorable, label excluded (`outcome_canceled`); o05 and o06 do not count each other in approvals |
| o07 | 3 items (seller `s_b` **twice**, new seller `s_c`), **3 payments**, **2 reviews**, 2 categories | 3 items, item value 130.00, freight 25.00, paid 155.00, 2 reviews (mean 3.0); 2 order-seller rows; seller features weighted by distinct seller |
| o08 | `s_b` history (on time); a second review with invalid score `7` | review quarantined `invalid_review_score` |
| o09 | Status `shipped`, no delivery date (outcome unknown) | scorable; label excluded `outcome_not_observed` |
| o10 | **No approval timestamp**; customer with **missing zip prefix**; review id shared with o11 | not scorable (`missing_approval`), never replaced by purchase time |
| o11 | Approval **before** purchase (incoherent date) | not scorable (`approval_before_purchase`) |
| o12 | Delivered **before** approval | scorable; label excluded (`delivery_before_approval`) |
| o13 | Product with **missing category** and product with **untranslated** category (`pc_gamer`) | category `missing_category` / `pc_gamer`; main category `pc_gamer` (highest price) |
| o14 | **Invalid purchase timestamp** `2018-02-30 10:00:00` | order quarantined; its item and payment cascade (`parent_order_quarantined`) |
| o15 | Status delivered but **no delivery date** | label excluded `outcome_delivered_missing_date` |

Other cases:

- `product_category_name_translation.csv` starts with a UTF-8 BOM (as some exports do).
- Zip prefix `1037` (leading zero lost) normalises to `01037`; the raw value is kept.
- Geolocation prefix `01037` has several distinct points, one exact duplicate and one outlier
  outside Brazil (excluded from the median centroid).
- Review `r07b` contains a quoted comma and a line break (multi-line CSV record).
- Payment `o05#1` is duplicated identically (strict duplicate).
