-- =====================================================================================
-- evaluation.order_labels — one row per order: outcome population, eligibility and target.
--
-- Definitions (docs/metric_definitions.md):
--   prediction_at  = order_approved_at (never replaced by the purchase timestamp)
--   promised_date  = civil date of order_estimated_delivery_date (stored at midnight)
--   delivered_date = civil date of order_delivered_customer_date
--   is_late        = delivered_date > promised_date   (a delivery on the afternoon of the
--                    promised day is ON TIME: dates are compared, not timestamps)
--   delay_days     = delivered_date - promised_date   (negative = early)
--
-- Two eligibility levels:
--   scoring_eligible : uses ONLY information available at approval -> the population that
--                      receives features and scores, whatever its final status;
--   label_eligible   : scoring_eligible AND a delivery outcome is observed and coherent ->
--                      the population on which performance is measured.
-- label_available_at is the actual customer delivery: the conservative moment from which
-- the outcome may be used (seller histories, training cut-offs).
-- =====================================================================================

DROP TABLE IF EXISTS evaluation.order_labels;
CREATE TABLE evaluation.order_labels AS
WITH base AS (
    SELECT
        o.order_id,
        o.order_status,
        o.purchase_at,
        o.approved_at,
        o.delivered_customer_at,
        o.purchase_at::date            AS purchase_date,
        o.estimated_delivery_at::date  AS promised_date,
        o.delivered_customer_at::date  AS delivered_date,
        EXISTS (SELECT 1 FROM core.order_items AS i WHERE i.order_id = o.order_id) AS has_items
    FROM core.orders AS o
),
classified AS (
    SELECT
        b.*,
        CASE
            WHEN b.order_status = 'delivered' AND b.delivered_customer_at IS NOT NULL THEN 'delivered'
            WHEN b.order_status = 'delivered'   THEN 'delivered_missing_date'
            WHEN b.order_status = 'canceled'    THEN 'canceled'
            WHEN b.order_status = 'unavailable' THEN 'unavailable'
            WHEN b.order_status IN ('created', 'approved', 'invoiced', 'processing', 'shipped') THEN 'not_observed'
            ELSE 'unknown_status'
        END AS outcome_population,
        CASE  -- first failing rule; information available at approval only
            WHEN b.approved_at IS NULL                  THEN 'missing_approval'
            WHEN b.promised_date IS NULL                THEN 'missing_promised_date'
            WHEN NOT b.has_items                        THEN 'no_items'
            WHEN b.approved_at < b.purchase_at          THEN 'approval_before_purchase'
            WHEN b.promised_date < b.purchase_date      THEN 'promise_before_purchase'
            WHEN b.promised_date < b.approved_at::date  THEN 'promise_before_approval'
        END AS scoring_exclusion_reason
    FROM base AS b
),
labelled AS (
    SELECT
        c.*,
        CASE
            WHEN c.scoring_exclusion_reason IS NOT NULL       THEN c.scoring_exclusion_reason
            WHEN c.outcome_population <> 'delivered'          THEN 'outcome_' || c.outcome_population
            WHEN c.delivered_customer_at < c.approved_at      THEN 'delivery_before_approval'
        END AS label_exclusion_reason
    FROM classified AS c
)
SELECT
    order_id,
    approved_at                              AS prediction_at,
    purchase_at,
    purchase_date,
    promised_date,
    delivered_date,
    outcome_population,
    scoring_exclusion_reason IS NULL         AS scoring_eligible,
    scoring_exclusion_reason,
    label_exclusion_reason IS NULL           AS label_eligible,
    label_exclusion_reason,
    CASE WHEN label_exclusion_reason IS NULL THEN delivered_customer_at END        AS label_available_at,
    CASE WHEN label_exclusion_reason IS NULL THEN delivered_date > promised_date END AS is_late,
    CASE WHEN label_exclusion_reason IS NULL THEN delivered_date - promised_date END AS delay_days
FROM labelled;

ALTER TABLE evaluation.order_labels ADD PRIMARY KEY (order_id);
CREATE INDEX ON evaluation.order_labels (prediction_at);
