# Assertions — features (real)

Checked at 2026-10-17 17:37 UTC. Status: **PUBLISHED**.

Critical checks block publication when violated; warnings are counted and reported.

| check | severity | violations | result | description |
|---|---|---|---|---|
| feature_grain | critical | 0 | pass | One feature row per order and feature version |
| feature_population | critical | 0 | pass | Every scorable order has features (regardless of final status) |
| forbidden_feature_columns | critical | 0 | pass | No outcome, review, payment, status or raw identifier column in the matrix |
| history_counts_coherent | critical | 0 | pass | History counters are internally consistent |
| history_grain | critical | 0 | pass | One history row per distinct order-seller pair |
| outcome_events_known_after_approval | critical | 0 | pass | An outcome is never known before its own order was approved |
| orders_without_distance | warning | 490 | FAIL | Scorable orders without a computable customer-seller distance |
| sellerless_scorable_orders | warning | 0 | pass | Scorable orders without seller (should be 0: no_items orders are not scorable) |
| orders_with_new_seller | info | 5,529 | info | Scorable orders with at least one seller without known outcome history |
