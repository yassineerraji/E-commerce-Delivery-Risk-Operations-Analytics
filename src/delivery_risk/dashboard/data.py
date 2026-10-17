"""Read-only data access for the dashboard.

- connects with the read-only role, in read-only transactions;
- every user input is passed as a bound parameter; identifiers used for grouping or sorting
  come from allow-lists, never from the request;
- results are cached in memory, keyed by query, parameters and the data version (latest
  publication id), so a new transform/feature build/evaluation invalidates the cache;
- no feature is rebuilt and no model is trained here.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from datetime import date
from typing import Any

import pandas as pd
from psycopg import sql

from delivery_risk.config import project_config
from delivery_risk.db import connect

SEGMENT_DIMENSIONS = {  # UI value -> column of mart.order_summary
    "customer_region": "customer_region",
    "customer_state": "customer_state",
    "main_category": "main_category",
}
REVIEW_SORT_COLUMNS = {  # UI value -> SQL expression owner/column (allow-list)
    "risk_rank": ("d", "risk_rank"),
    "score_calibrated": ("d", "score_calibrated"),
    "promised_lead_days": ("f", "promised_lead_days"),
    "item_value_brl": ("f", "item_value_brl"),
    "n_distinct_sellers": ("f", "n_distinct_sellers"),
    "sh_known_outcomes_total_min": ("f", "sh_known_outcomes_total_min"),
}
MAX_PAGE_SIZE = 50

# Filtered order population shared by the overview queries. Static SQL text; filters are
# bound parameters. NULL array = no filter. The category filter selects orders CONTAINING at
# least one item of the selected categories (EXISTS), so each order is counted once.
POPULATION_CTE = """
pop AS (
    SELECT o.*
    FROM mart.order_summary AS o
    WHERE o.purchase_at >= %(start)s AND o.purchase_at < %(end)s
      AND (%(regions)s::text[] IS NULL OR o.customer_region = ANY(%(regions)s::text[]))
      AND (%(categories)s::text[] IS NULL OR EXISTS (
            SELECT 1 FROM mart.order_categories AS c
            WHERE c.order_id = o.order_id AND c.category = ANY(%(categories)s::text[])))
)
"""
POP = sql.SQL(POPULATION_CTE)


class DashboardData:
    def __init__(self, mode: str, cache_size: int = 256, version_ttl_s: float = 10.0):
        self.mode = mode
        self.min_support = int(project_config()["analysis"]["min_support_for_ranking"])
        self.min_positives = int(project_config()["analysis"]["min_positives_for_group_metrics"])
        self.feature_version = project_config()["project"]["feature_version"]
        self._cache: OrderedDict[tuple, pd.DataFrame] = OrderedDict()
        self._cache_size = cache_size
        self._version: tuple[float, int] | None = None
        self._version_ttl = version_ttl_s

    # ------------------------------------------------------------------ plumbing
    def _run(self, query: str | sql.Composable, params: dict | tuple | None = None) -> pd.DataFrame:
        with connect(self.mode, role="reader") as conn:
            conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            conn.execute("SET statement_timeout = '15s'")
            with conn.cursor() as cur:
                cur.execute(query, params)
                cols = [d.name for d in cur.description]
                return pd.DataFrame(cur.fetchall(), columns=cols)

    def data_version(self) -> int:
        now = time.monotonic()
        if self._version is None or now - self._version[0] > self._version_ttl:
            df = self._run("SELECT coalesce(max(publication_id), 0) AS v FROM meta.publication_log")
            self._version = (now, int(df.v.iloc[0]))
        return self._version[1]

    def _cached(self, name: str, query, params: dict | None = None) -> pd.DataFrame:
        key = (
            name,
            tuple(
                sorted(
                    (k, tuple(v) if isinstance(v, list) else v) for k, v in (params or {}).items()
                )
            ),
            self.data_version(),
        )
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key].copy()
        df = self._run(query, params)
        self._cache[key] = df
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return df.copy()

    @staticmethod
    def _filters(
        start: date, end_inclusive: date, regions: list[str] | None, categories: list[str] | None
    ) -> dict:
        return {
            "start": start,
            "end": pd.Timestamp(end_inclusive) + pd.Timedelta(days=1),
            "regions": regions or None,
            "categories": categories or None,
        }

    # ------------------------------------------------------------------ metadata
    def latest_run(self) -> dict | None:
        df = self._cached(
            "latest_run",
            """
            SELECT run_id, created_at, status, selected_model, feature_version, feature_sha256, split_sha256,
                   manifest -> 'splits' AS splits, manifest ->> 'data_mode' AS data_mode
            FROM evaluation.model_runs WHERE status = 'evaluated' ORDER BY created_at DESC LIMIT 1""",
        )
        return None if df.empty else df.iloc[0].to_dict()

    def publications(self) -> pd.DataFrame:
        return self._cached(
            "publications",
            """
            SELECT DISTINCT ON (layer) layer, published_at, data_mode
            FROM meta.publication_log ORDER BY layer, published_at DESC""",
        )

    def filter_options(self) -> dict[str, Any]:
        regions = self._cached(
            "regions",
            "SELECT DISTINCT customer_region FROM mart.order_summary WHERE customer_region IS NOT NULL ORDER BY 1",
        )
        cats = self._cached(
            "categories",
            "SELECT category, count(*) AS n FROM mart.order_categories GROUP BY 1 ORDER BY n DESC, 1",
        )
        rng = self._cached(
            "date_range",
            "SELECT min(purchase_at)::date AS lo, max(purchase_at)::date AS hi FROM mart.order_summary",
        )
        return {
            "regions": regions.customer_region.tolist(),
            "categories": cats.category.tolist(),
            "min_date": rng.lo.iloc[0],
            "max_date": rng.hi.iloc[0],
        }

    # ------------------------------------------------------------------ page A: operations overview
    def overview_kpis(self, start, end, regions=None, categories=None) -> dict:
        df = self._cached(
            "kpis",
            sql.SQL("""WITH {pop}
            SELECT count(*) AS orders,
                   sum(item_value_brl) AS item_value_brl,
                   sum(freight_value_brl) AS freight_value_brl,
                   count(*) FILTER (WHERE label_eligible) AS eligible_delivered,
                   count(*) FILTER (WHERE is_late) AS late,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY delivery_duration_days) FILTER (WHERE label_eligible) AS median_delivery_days,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY promised_lead_days) FILTER (WHERE label_eligible) AS median_promised_days,
                   count(*) FILTER (WHERE outcome_population IN ('canceled', 'unavailable')) AS canceled_or_unavailable,
                   count(*) FILTER (WHERE outcome_population NOT IN ('delivered', 'canceled', 'unavailable')) AS outcome_unknown
            FROM pop""").format(pop=POP),
            self._filters(start, end, regions, categories),
        )
        return df.iloc[0].to_dict()

    def overview_monthly(self, start, end, regions=None, categories=None) -> pd.DataFrame:
        return self._cached(
            "monthly",
            sql.SQL("""WITH {pop}
            SELECT purchase_month,
                   count(*) AS orders,
                   count(*) FILTER (WHERE label_eligible) AS eligible_delivered,
                   count(*) FILTER (WHERE is_late) AS late,
                   count(*) FILTER (WHERE is_late)::float / NULLIF(count(*) FILTER (WHERE label_eligible), 0) AS late_rate,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY promised_lead_days) FILTER (WHERE label_eligible) AS median_promised_days,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY delivery_duration_days) FILTER (WHERE label_eligible) AS median_delivery_days
            FROM pop GROUP BY purchase_month ORDER BY purchase_month""").format(pop=POP),
            self._filters(start, end, regions, categories),
        )

    def overview_segments(
        self, dimension: str, start, end, regions=None, categories=None
    ) -> pd.DataFrame:
        column = SEGMENT_DIMENSIONS.get(dimension)
        if column is None:
            raise ValueError(f"Unsupported segment dimension: {dimension!r}")
        query = sql.SQL("""WITH {pop}
            SELECT coalesce({col}::text, 'Unknown') AS segment,
                   count(*) AS orders,
                   count(*) FILTER (WHERE label_eligible) AS eligible_delivered,
                   count(*) FILTER (WHERE is_late) AS late,
                   count(*) FILTER (WHERE is_late)::float / NULLIF(count(*) FILTER (WHERE label_eligible), 0) AS late_rate
            FROM pop GROUP BY 1 ORDER BY eligible_delivered DESC, segment""").format(
            pop=POP, col=sql.Identifier(column)
        )
        return self._cached(
            f"segments:{column}", query, self._filters(start, end, regions, categories)
        )

    def seller_exposure(
        self, start, end, regions=None, categories=None, limit: int = 15
    ) -> pd.DataFrame:
        params = self._filters(start, end, regions, categories) | {
            "min_support": self.min_support,
            "limit": limit,
        }
        return self._cached(
            "seller_exposure",
            sql.SQL("""WITH {pop}
            SELECT e.seller_id, e.seller_state,
                   count(*) AS orders,
                   count(*) FILTER (WHERE e.label_eligible) AS eligible_delivered,
                   count(*) FILTER (WHERE e.is_late) AS late_orders,
                   count(*) FILTER (WHERE e.is_late)::float / NULLIF(count(*) FILTER (WHERE e.label_eligible), 0) AS late_order_exposure,
                   avg(e.is_multi_seller_order::int)::float AS multi_seller_share
            FROM mart.seller_order_exposure AS e
            JOIN pop USING (order_id)
            GROUP BY e.seller_id, e.seller_state
            HAVING count(*) FILTER (WHERE e.label_eligible) >= %(min_support)s
            ORDER BY late_order_exposure DESC, eligible_delivered DESC, e.seller_id
            LIMIT %(limit)s""").format(pop=POP),
            params,
        )

    # ------------------------------------------------------------------ page B: historical risk review
    def review_days(self, run_id: str, model: str) -> pd.DataFrame:
        return self._cached(
            "review_days",
            """
            SELECT prediction_date, count(*) AS scorable
            FROM evaluation.predictions
            WHERE run_id = %(run)s AND model_name = %(model)s AND block = 'test'
            GROUP BY prediction_date ORDER BY prediction_date""",
            {"run": run_id, "model": model},
        )

    def review_table(
        self,
        run_id: str,
        model: str,
        day: date,
        q: float,
        retrospective: bool,
        sort: str = "risk_rank",
        descending: bool = False,
        page: int = 0,
        page_size: int = 15,
    ) -> pd.DataFrame:
        owner, column = REVIEW_SORT_COLUMNS.get(sort, REVIEW_SORT_COLUMNS["risk_rank"])
        direction = sql.SQL("DESC NULLS LAST" if descending else "ASC NULLS LAST")
        page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        # Two distinct SELECT lists: the "At approval" query never reads outcome columns.
        outcome_cols = (
            sql.SQL(", l.outcome_population, l.delivered_date, l.is_late, l.delay_days")
            if retrospective
            else sql.SQL("")
        )
        outcome_join = (
            sql.SQL("LEFT JOIN evaluation.order_labels AS l ON l.order_id = d.order_id")
            if retrospective
            else sql.SQL("")
        )
        query = sql.SQL("""
            WITH d AS (
                SELECT p.order_id, p.score_raw, p.score_calibrated,
                       row_number() OVER (ORDER BY p.score_raw DESC, p.order_id) AS risk_rank,
                       count(*) OVER () AS n_day
                FROM evaluation.predictions AS p
                WHERE p.run_id = %(run)s AND p.model_name = %(model)s AND p.prediction_date = %(day)s
            )
            SELECT d.risk_rank, d.order_id, d.score_calibrated, d.n_day,
                   d.risk_rank <= ceil(%(q)s * d.n_day) AS selected,
                   f.promised_lead_days, f.item_value_brl, f.n_distinct_sellers,
                   f.sh_known_outcomes_total_min, f.customer_state, f.main_category {outcome_cols}
            FROM d
            JOIN feature.order_features AS f ON f.order_id = d.order_id AND f.feature_version = %(fv)s
            {outcome_join}
            ORDER BY {owner}.{column} {direction}, d.order_id
            LIMIT %(limit)s OFFSET %(offset)s""").format(
            outcome_cols=outcome_cols,
            outcome_join=outcome_join,
            owner=sql.Identifier(owner),
            column=sql.Identifier(column),
            direction=direction,
        )
        params = {
            "run": run_id,
            "model": model,
            "day": day,
            "q": q,
            "fv": self.feature_version,
            "limit": page_size,
            "offset": max(0, int(page)) * page_size,
        }
        return self._cached(f"review_table:{retrospective}:{sort}:{descending}", query, params)

    def review_summary(
        self,
        run_id: str,
        model: str,
        q: float,
        day: date | None = None,
        with_outcomes: bool = False,
    ) -> dict:
        """Selection counts for one day (or the whole test block).

        Selection = top ceil(q * N_day) per day by score, decided without outcomes. Outcome
        counts are only queried when with_outcomes=True (retrospective mode).
        """
        params = {"run": run_id, "model": model, "q": q, "day": day}
        if not with_outcomes:
            df = self._cached(
                "review_summary_at_approval",
                """
                WITH ranked AS (
                    SELECT p.prediction_date,
                           row_number() OVER (PARTITION BY p.prediction_date ORDER BY p.score_raw DESC, p.order_id) AS rk,
                           count(*) OVER (PARTITION BY p.prediction_date) AS n_day
                    FROM evaluation.predictions AS p
                    WHERE p.run_id = %(run)s AND p.model_name = %(model)s AND p.block = 'test'
                      AND (%(day)s::date IS NULL OR p.prediction_date = %(day)s::date)
                )
                SELECT count(*) AS scorable, count(DISTINCT prediction_date) AS days,
                       count(*) FILTER (WHERE rk <= ceil(%(q)s * n_day)) AS selected
                FROM ranked""",
                params,
            )
            return df.iloc[0].to_dict()
        df = self._cached(
            "review_summary_retrospective",
            """
            WITH ranked AS (
                SELECT p.prediction_date, p.order_id,
                       row_number() OVER (PARTITION BY p.prediction_date ORDER BY p.score_raw DESC, p.order_id) AS rk,
                       count(*) OVER (PARTITION BY p.prediction_date) AS n_day
                FROM evaluation.predictions AS p
                WHERE p.run_id = %(run)s AND p.model_name = %(model)s AND p.block = 'test'
                  AND (%(day)s::date IS NULL OR p.prediction_date = %(day)s::date)
            )
            SELECT count(*) AS scorable,
                   count(DISTINCT r.prediction_date) AS days,
                   count(*) FILTER (WHERE r.rk <= ceil(%(q)s * r.n_day)) AS selected,
                   count(*) FILTER (WHERE r.rk <= ceil(%(q)s * r.n_day) AND l.label_eligible) AS selected_known,
                   count(*) FILTER (WHERE r.rk <= ceil(%(q)s * r.n_day) AND l.is_late) AS selected_late,
                   count(*) FILTER (WHERE l.label_eligible) AS known,
                   count(*) FILTER (WHERE l.is_late) AS late
            FROM ranked AS r JOIN evaluation.order_labels AS l USING (order_id)""",
            params,
        )
        return df.iloc[0].to_dict()

    # ------------------------------------------------------------------ page C: model & data quality
    def model_metrics(self, run_id: str) -> pd.DataFrame:
        return self._cached(
            "model_metrics",
            """
            SELECT model_name, variant, metric, value, ci_low, ci_high, n, positives, note
            FROM evaluation.model_metrics WHERE run_id = %(run)s""",
            {"run": run_id},
        )

    def calibration_bins(self, run_id: str, model: str) -> pd.DataFrame:
        return self._cached(
            "calibration_bins",
            """
            SELECT variant, bin, mean_score, observed_rate, n FROM evaluation.calibration_bins
            WHERE run_id = %(run)s AND model_name = %(model)s ORDER BY variant, bin""",
            {"run": run_id, "model": model},
        )

    def metrics_by_group(self, run_id: str, model: str, group_type: str) -> pd.DataFrame:
        return self._cached(
            "metrics_by_group",
            """
            SELECT group_value, n, positives, prevalence, average_precision, precision_at_10, lift_at_10
            FROM evaluation.metrics_by_group
            WHERE run_id = %(run)s AND model_name = %(model)s AND group_type = %(gt)s ORDER BY group_value""",
            {"run": run_id, "model": model, "gt": group_type},
        )

    def assertions(self) -> pd.DataFrame:
        return self._cached(
            "assertions",
            """
            SELECT layer, check_name, severity, violation_count, description FROM quality.assertion_results
            ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, layer, check_name""",
        )

    def quarantine_summary(self) -> pd.DataFrame:
        return self._cached(
            "quarantine",
            "SELECT dataset, reason, count(*) AS rows FROM quality.quarantine GROUP BY 1, 2 ORDER BY 3 DESC",
        )

    def coverage(self) -> pd.DataFrame:
        return self._cached(
            "coverage",
            """
            SELECT outcome_population, scoring_status, label_status, sum(n)::int AS orders
            FROM mart.label_coverage GROUP BY 1, 2, 3 ORDER BY orders DESC""",
        )
