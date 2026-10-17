"""Data-quality and coverage reports (read-only).

`coverage.md` is the audit used to fix split boundaries: it reports volumes, label coverage,
label delays and censoring per month and per proposed block, and never looks at any model.
"""

from __future__ import annotations

import csv
import logging
from datetime import UTC, datetime

from delivery_risk.config import load_splits, mode_config
from delivery_risk.db import connect, fetch_all
from delivery_risk.mdtable import md_table
from delivery_risk.pipeline import run_assertions

log = logging.getLogger(__name__)


def _q(conn, query, params=None):
    return fetch_all(conn, query, params)


def write_quality_reports(mode: str) -> None:
    cfg = mode_config(mode)
    out = cfg.report_dir("data_quality")
    banner = (
        "> **SYNTHETIC DATA** — generated for the automated tests; not the real Olist dataset.\n"
        if cfg.is_synthetic
        else ""
    )
    with connect(mode) as conn:
        # Re-run the transform/feature assertions read-only so the report reflects the current state.
        transform = run_assertions(conn, "transform")
        features = (
            run_assertions(conn, "features")
            if _q(conn, "SELECT to_regclass('feature.order_features')")[1][0][0]
            else []
        )
        conn.rollback()  # do not alter stored results from a reporting command

        manifest = _q(
            conn,
            """
            SELECT m.dataset, m.file_name, m.file_size_bytes, left(m.sha256, 16) || '…', m.row_count, m.parse_errors,
                   m.source_version, m.acquired_at::date, m.acquired_at_basis, m.loaded_at, m.load_id, m.batch_id
            FROM meta.load_manifest m WHERE m.load_id = meta.current_load_id(m.dataset) ORDER BY m.dataset""",
        )
        quarantine = _q(
            conn,
            "SELECT dataset, reason, count(*) FROM quality.quarantine GROUP BY 1, 2 ORDER BY 1, 3 DESC",
        )
        duplicates = _q(
            conn,
            "SELECT dataset, kind, count(*), sum(n_rows) FROM quality.duplicate_log GROUP BY 1, 2 ORDER BY 1, 2",
        )
        layer_counts = _q(
            conn,
            """
            SELECT 'core.orders', count(*) FROM core.orders UNION ALL
            SELECT 'core.order_items', count(*) FROM core.order_items UNION ALL
            SELECT 'core.order_sellers', count(*) FROM core.order_sellers UNION ALL
            SELECT 'core.payments', count(*) FROM core.payments UNION ALL
            SELECT 'core.reviews', count(*) FROM core.reviews UNION ALL
            SELECT 'core.customers', count(*) FROM core.customers UNION ALL
            SELECT 'core.sellers', count(*) FROM core.sellers UNION ALL
            SELECT 'core.products', count(*) FROM core.products UNION ALL
            SELECT 'core.geo_prefix', count(*) FROM core.geo_prefix UNION ALL
            SELECT 'mart.order_summary', count(*) FROM mart.order_summary UNION ALL
            SELECT 'mart.seller_order_exposure', count(*) FROM mart.seller_order_exposure""",
        )
        exclusions = _q(
            conn,
            """
            SELECT outcome_population, scoring_status, label_status, sum(n)::int AS orders,
                   round(100.0 * sum(n) / sum(sum(n)) OVER (), 2) AS pct
            FROM mart.label_coverage GROUP BY 1, 2, 3 ORDER BY orders DESC""",
        )
        ranges = _q(
            conn,
            """
            SELECT 'purchase_at', min(purchase_at), max(purchase_at), count(purchase_at) FROM core.orders UNION ALL
            SELECT 'approved_at', min(approved_at), max(approved_at), count(approved_at) FROM core.orders UNION ALL
            SELECT 'delivered_customer_at', min(delivered_customer_at), max(delivered_customer_at), count(delivered_customer_at) FROM core.orders UNION ALL
            SELECT 'estimated_delivery_at', min(estimated_delivery_at), max(estimated_delivery_at), count(estimated_delivery_at) FROM core.orders""",
        )
        monthly_cols, monthly = _q(
            conn,
            """
            SELECT date_trunc('month', prediction_at)::date AS approval_month,
                   count(*) AS scorable,
                   count(*) FILTER (WHERE label_eligible) AS labelled,
                   round(count(*) FILTER (WHERE label_eligible)::numeric / count(*), 4) AS label_coverage,
                   count(*) FILTER (WHERE outcome_population = 'not_observed') AS outcome_not_observed,
                   count(*) FILTER (WHERE outcome_population IN ('canceled', 'unavailable')) AS canceled_or_unavailable,
                   count(*) FILTER (WHERE is_late) AS late,
                   round(count(*) FILTER (WHERE is_late)::numeric / NULLIF(count(*) FILTER (WHERE label_eligible), 0), 4) AS late_rate,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM label_available_at - prediction_at) / 86400)
                       FILTER (WHERE label_eligible) AS label_delay_p50_days,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM label_available_at - prediction_at) / 86400)
                       FILTER (WHERE label_eligible) AS label_delay_p95_days
            FROM evaluation.order_labels WHERE scoring_eligible
            GROUP BY 1 ORDER BY 1""",
        )
        delay = _q(
            conn,
            """
            SELECT percentile_cont(ARRAY[0.5, 0.9, 0.95, 0.99]) WITHIN GROUP (ORDER BY extract(epoch FROM label_available_at - prediction_at) / 86400)
            FROM evaluation.order_labels WHERE label_eligible""",
        )[1][0][0]
        splits = load_splits(mode)
        block_rows = []
        for i, b in enumerate(splits.blocks):
            nxt = splits.blocks[i + 1].start if i + 1 < len(splits.blocks) else None
            row = conn.execute(
                """
                SELECT count(*), count(*) FILTER (WHERE label_eligible), count(*) FILTER (WHERE is_late),
                       count(*) FILTER (WHERE label_eligible AND label_available_at < %(nxt)s)
                FROM evaluation.order_labels
                WHERE scoring_eligible AND prediction_at >= %(s)s AND prediction_at < %(e)s""",
                {"s": b.start, "e": b.end, "nxt": nxt or datetime.max},
            ).fetchone()
            block_rows.append(
                (
                    b.name,
                    b.start.date(),
                    b.end.date(),
                    row[0],
                    row[1],
                    row[2],
                    round(row[2] / row[1], 4) if row[1] else None,
                    row[3] if nxt else "n/a (final evaluation uses all observed labels)",
                )
            )

    with open(out / "coverage_monthly.csv", "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(monthly_cols)
        writer.writerows(monthly)

    now = f"{datetime.now(UTC):%Y-%m-%d %H:%M UTC}"
    sev = {"critical": 0, "warning": 1, "info": 2}
    assert_rows = [
        (
            layer,
            r.check_name,
            r.severity,
            r.violation_count,
            "info" if r.severity == "info" else ("FAIL" if r.failed else "pass"),
        )
        for layer, results in (("transform", transform), ("features", features))
        for r in sorted(results, key=lambda r: (sev[r.severity], r.check_name))
    ]
    report = [
        f"# Data quality report — {mode}",
        "",
        banner,
        f"Generated {now} from the published tables.",
        "",
        "## 1. Source files (current load)",
        "",
        md_table(
            [
                "dataset",
                "file",
                "bytes",
                "sha256",
                "rows",
                "parse errors",
                "source version",
                "acquired",
                "acquired basis",
                "loaded at",
                "load id",
                "batch",
            ],
            manifest[1],
        ),
        "Row counts exclude the header. `acquired basis = file_mtime` means the acquisition date is the file "
        "modification time, not a date declared by the user (`--acquired-on`).",
        "",
        "## 2. Published row counts",
        "",
        md_table(["table", "rows"], layer_counts[1]),
        "## 3. Assertions",
        "",
        md_table(["layer", "check", "severity", "violations", "result"], assert_rows),
        "## 4. Quarantine (rows rejected with a reason)",
        "",
        md_table(["dataset", "reason", "rows"], quarantine[1]),
        "## 5. Duplicate keys",
        "",
        "`strict_duplicate`: identical rows, one kept. `key_conflict`: different contents for the same key "
        "(critical, blocks publication).",
        "",
        md_table(["dataset", "kind", "keys", "rows involved"], duplicates[1]),
        "## 6. Outcome populations and exclusions",
        "",
        "Every order appears once. Canceled, unavailable and undelivered orders are separate populations, never "
        "counted as on time.",
        "",
        md_table(
            ["outcome population", "scoring status", "label status", "orders", "% of orders"],
            exclusions[1],
        ),
    ]
    (out / "data_quality_report.md").write_text("\n".join(report))

    coverage = [
        f"# Coverage audit — {mode}",
        "",
        banner,
        f"Generated {now}. Used to fix split boundaries; contains no model result.",
        "",
        "## Date ranges in the source",
        "",
        md_table(["column", "min", "max", "non-null"], ranges[1]),
        "## Label availability delay (approval → customer delivery, eligible orders)",
        "",
        md_table(
            ["p50 days", "p90 days", "p95 days", "p99 days"],
            [tuple(round(float(x), 1) for x in delay)] if delay else [],
        ),
        "## Monthly coverage by approval month (scorable orders)",
        "",
        "`label_coverage` = share of scorable orders with an eligible observed outcome. A drop in the last months "
        "signals right censoring at the end of the source.",
        "",
        md_table([c for c in monthly_cols], monthly),
        f"## Proposed blocks (`config/splits.yaml`, status: **{splits.status}**, sha256 `{splits.sha256[:12]}…`)",
        "",
        md_table(
            [
                "block",
                "start",
                "end (excl.)",
                "scorable",
                "labelled",
                "late",
                "late rate",
                "labels known before next block",
            ],
            block_rows,
        ),
        "Labels known before the next block are the only ones usable to fit/select/calibrate at that point "
        "(label availability = actual customer delivery).",
        "",
    ]
    (out / "coverage.md").write_text("\n".join(coverage))
    log.info("Reports written: %s, %s", out / "data_quality_report.md", out / "coverage.md")
