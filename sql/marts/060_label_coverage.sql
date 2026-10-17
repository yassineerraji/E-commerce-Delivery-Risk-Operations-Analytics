-- mart.label_coverage — exclusion and outcome coverage table, one row per
-- (purchase month, outcome population, scoring status, label status).
-- Every order appears exactly once: sum(n) = count(core.orders).

DROP TABLE IF EXISTS mart.label_coverage;
CREATE TABLE mart.label_coverage AS
SELECT
    date_trunc('month', purchase_at)::date             AS purchase_month,
    outcome_population,
    scoring_eligible,
    coalesce(scoring_exclusion_reason, 'scorable')      AS scoring_status,
    coalesce(label_exclusion_reason, 'label_eligible')  AS label_status,
    count(*)                                            AS n
FROM evaluation.order_labels
GROUP BY 1, 2, 3, 4, 5;
