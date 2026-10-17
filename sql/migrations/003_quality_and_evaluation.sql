-- 003_quality_and_evaluation.sql — persistent tables for data quality and model outputs.
-- quality.* tables describe the currently published transform (rebuilt by each transform).
-- evaluation.* run tables keep every training/evaluation run (rows are replaced per run_id).

CREATE TABLE IF NOT EXISTS quality.quarantine (
    dataset     text    NOT NULL,
    load_id     bigint,
    source_line integer,
    reason      text    NOT NULL,
    raw_record  jsonb   NOT NULL
);

-- Keys seen more than once: identical rows (strict duplicates, resolved by keeping one) or
-- different versions of the same key (conflicts, unresolved and therefore critical).
CREATE TABLE IF NOT EXISTS quality.duplicate_log (
    dataset   text    NOT NULL,
    key_value text    NOT NULL,
    n_rows    integer NOT NULL,
    kind      text    NOT NULL CHECK (kind IN ('strict_duplicate', 'key_conflict'))
);

CREATE TABLE IF NOT EXISTS quality.assertion_results (
    check_name      text        NOT NULL,
    layer           text        NOT NULL,
    severity        text        NOT NULL CHECK (severity IN ('critical', 'warning', 'info')),
    violation_count bigint      NOT NULL,
    description     text        NOT NULL,
    checked_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (check_name, layer)
);

CREATE TABLE IF NOT EXISTS evaluation.model_runs (
    run_id          text PRIMARY KEY,
    data_mode       text        NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    status          text        NOT NULL CHECK (status IN ('trained', 'evaluated')),
    split_sha256    text        NOT NULL,
    feature_version text        NOT NULL,
    feature_sha256  text        NOT NULL,
    selected_model  text        NOT NULL,
    manifest        jsonb       NOT NULL
);

-- Frozen scores per order, model and run. block = temporal block of prediction_at.
CREATE TABLE IF NOT EXISTS evaluation.predictions (
    run_id           text        NOT NULL REFERENCES evaluation.model_runs (run_id) ON DELETE CASCADE,
    model_name       text        NOT NULL,
    order_id         text        NOT NULL,
    block            text        NOT NULL,
    prediction_at    timestamp   NOT NULL,
    prediction_date  date        NOT NULL,
    score_raw        double precision NOT NULL,
    score_calibrated double precision,
    PRIMARY KEY (run_id, model_name, order_id)
);

-- Long-format metrics: one row per run, model, block, variant and metric.
CREATE TABLE IF NOT EXISTS evaluation.model_metrics (
    run_id     text NOT NULL REFERENCES evaluation.model_runs (run_id) ON DELETE CASCADE,
    model_name text NOT NULL,
    block      text NOT NULL,
    variant    text NOT NULL,
    metric     text NOT NULL,
    value      double precision,
    ci_low     double precision,
    ci_high    double precision,
    n          integer,
    positives  integer,
    note       text,
    PRIMARY KEY (run_id, model_name, block, variant, metric)
);

-- Daily review-capacity simulation: ceil(q * N_day) orders selected among all scorable orders.
CREATE TABLE IF NOT EXISTS evaluation.capacity_daily (
    run_id             text    NOT NULL REFERENCES evaluation.model_runs (run_id) ON DELETE CASCADE,
    model_name         text    NOT NULL,
    prediction_date    date    NOT NULL,
    capacity_fraction  numeric NOT NULL,
    n_scorable         integer NOT NULL,
    k_selected         integer NOT NULL,
    n_selected_labeled integer NOT NULL,
    n_selected_late    integer NOT NULL,
    n_day_labeled      integer NOT NULL,
    n_day_late         integer NOT NULL,
    PRIMARY KEY (run_id, model_name, prediction_date, capacity_fraction)
);

CREATE TABLE IF NOT EXISTS evaluation.calibration_bins (
    run_id        text    NOT NULL REFERENCES evaluation.model_runs (run_id) ON DELETE CASCADE,
    model_name    text    NOT NULL,
    variant       text    NOT NULL,
    bin           integer NOT NULL,
    mean_score    double precision NOT NULL,
    observed_rate double precision NOT NULL,
    n             integer NOT NULL,
    PRIMARY KEY (run_id, model_name, variant, bin)
);

-- Metrics by month or segment on the test block (with sample sizes).
CREATE TABLE IF NOT EXISTS evaluation.metrics_by_group (
    run_id            text NOT NULL REFERENCES evaluation.model_runs (run_id) ON DELETE CASCADE,
    model_name        text NOT NULL,
    group_type        text NOT NULL,
    group_value       text NOT NULL,
    n                 integer NOT NULL,
    positives         integer NOT NULL,
    prevalence        double precision,
    average_precision double precision,
    precision_at_10   double precision,
    lift_at_10        double precision,
    PRIMARY KEY (run_id, model_name, group_type, group_value)
);
