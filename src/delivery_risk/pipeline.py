"""SQL pipeline orchestration: transform (staging -> core -> marts) and feature build.

Each step runs in ONE transaction. Assertions run before commit; a critical violation rolls
back the whole step so readers keep seeing the previously published tables.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

from delivery_risk.config import SQL_DIR, mode_config, project_config
from delivery_risk.db import fetch_all, run_sql_dir, set_local_settings, sql_dir_sha256, transaction
from delivery_risk.features.catalog import FEATURE_NAMES, KEY_COLUMNS
from delivery_risk.ingestion.contracts import CONTRACTS
from delivery_risk.mdtable import md_table

log = logging.getLogger(__name__)


class PublicationBlocked(RuntimeError):
    """A critical data-quality assertion failed; nothing was published."""


@dataclass(frozen=True)
class AssertionResult:
    check_name: str
    severity: str
    violation_count: int
    description: str

    @property
    def failed(self) -> bool:
        return self.violation_count > 0


def sql_settings(mode: str) -> dict[str, object]:
    cfg = project_config()
    bbox = cfg["analysis"]["brazil_bbox"]
    return {
        "data_mode": mode,
        "feature_version": cfg["project"]["feature_version"],
        "min_support": cfg["analysis"]["min_support_for_ranking"],
        **bbox,
    }


def run_assertions(conn: psycopg.Connection, layer: str) -> list[AssertionResult]:
    _, rows = fetch_all(conn, (SQL_DIR / "assertions" / f"{layer}.sql").read_text())
    results = [AssertionResult(r[0], r[1], int(r[2]), r[3]) for r in rows]
    conn.execute("DELETE FROM quality.assertion_results WHERE layer = %s", (layer,))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO quality.assertion_results (check_name, layer, severity, violation_count, description)"
            " VALUES (%s, %s, %s, %s, %s)",
            [(r.check_name, layer, r.severity, r.violation_count, r.description) for r in results],
        )
    return results


def write_assertion_report(
    mode: str, layer: str, results: list[AssertionResult], published: bool
) -> None:
    path = mode_config(mode).report_dir("data_quality") / f"assertions_{layer}.md"
    status = (
        "PUBLISHED" if published else "BLOCKED — nothing was published (transaction rolled back)"
    )
    lines = [
        f"# Assertions — {layer} ({mode})",
        "",
        f"Checked at {datetime.now(UTC):%Y-%m-%d %H:%M UTC}. Status: **{status}**.",
        "",
        "Critical checks block publication when violated; warnings are counted and reported.",
        "",
        md_table(
            ["check", "severity", "violations", "result", "description"],
            [
                (
                    r.check_name,
                    r.severity,
                    r.violation_count,
                    "info" if r.severity == "info" else ("FAIL" if r.failed else "pass"),
                    r.description,
                )
                for r in sorted(
                    results,
                    key=lambda r: (
                        {"critical": 0, "warning": 1, "info": 2}[r.severity],
                        r.check_name,
                    ),
                )
            ],
        ),
    ]
    path.write_text("\n".join(lines))
    log.info("Report written: %s", path)


def _check_and_publish(
    conn: psycopg.Connection, mode: str, layer: str, details: dict
) -> list[AssertionResult]:
    results = run_assertions(conn, layer)
    critical = [r for r in results if r.severity == "critical" and r.failed]
    write_assertion_report(mode, layer, results, published=not critical)
    for r in results:
        if r.failed and r.severity != "info":
            log.log(
                logging.ERROR if r.severity == "critical" else logging.WARNING,
                "%-8s %-36s %d",
                r.severity.upper(),
                r.check_name,
                r.violation_count,
            )
    if critical:
        names = ", ".join(f"{r.check_name}={r.violation_count}" for r in critical)
        raise PublicationBlocked(
            f"{layer}: critical assertions failed ({names}); see reports/data_quality."
        )
    conn.execute(
        "INSERT INTO meta.publication_log (layer, data_mode, details) VALUES (%s, %s, %s)",
        (layer, mode, json.dumps(details)),
    )
    return results


def current_load_ids(conn: psycopg.Connection) -> dict[str, int | None]:
    return {
        c.dataset: conn.execute("SELECT meta.current_load_id(%s)", (c.dataset,)).fetchone()[0]
        for c in CONTRACTS
    }


def run_transform(mode: str) -> list[AssertionResult]:
    with transaction(mode) as conn:
        loads = current_load_ids(conn)
        missing = [d for d, load_id in loads.items() if load_id is None]
        if missing:
            raise RuntimeError(f"No loaded file for datasets {missing}; run the ingest step first.")
        set_local_settings(conn, sql_settings(mode))
        for layer_dir in ("staging", "core", "marts"):
            run_sql_dir(conn, SQL_DIR / layer_dir)
        details = {
            "load_ids": loads,
            "sql_sha256": sql_dir_sha256(SQL_DIR / "staging", SQL_DIR / "core", SQL_DIR / "marts"),
        }
        return _check_and_publish(conn, mode, "transform", details)


def run_features(mode: str) -> list[AssertionResult]:
    with transaction(mode) as conn:
        set_local_settings(conn, sql_settings(mode))
        run_sql_dir(conn, SQL_DIR / "features")
        _, rows = fetch_all(
            conn,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'feature' AND table_name = 'order_features' ORDER BY ordinal_position",
        )
        actual = [r[0] for r in rows]
        expected = [*KEY_COLUMNS, *FEATURE_NAMES]
        if actual != expected:
            raise PublicationBlocked(
                f"feature.order_features columns differ from the allow-list.\n expected={expected}\n actual={actual}"
            )
        details = {
            "feature_version": project_config()["project"]["feature_version"],
            "sql_sha256": sql_dir_sha256(SQL_DIR / "features"),
        }
        return _check_and_publish(conn, mode, "features", details)
