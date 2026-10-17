"""SQL benchmark in an isolated schema (`bench`) of the chosen mode's database.

For each query: inputs are copied into `bench` WITHOUT indexes, the initial plan is captured
with EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON), then each variant (index or rewrite) is measured
with one warm-up run and N timed runs. Results of every variant are compared (md5 of the
ordered result). Server execution time (EXPLAIN ANALYZE, TIMING OFF), client time (execute +
fetch, i.e. query + transfer) and Dash callback time are reported separately.

Only the `bench` schema is created/dropped; pipeline tables and indexes are never modified.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import statistics
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import psycopg

from delivery_risk.config import SQL_DIR, mode_config
from delivery_risk.db import connect
from delivery_risk.mdtable import md_table

log = logging.getLogger(__name__)

INPUTS = {
    "targets": """SELECT l.order_id, os.seller_id, l.prediction_at
                  FROM evaluation.order_labels l JOIN core.order_sellers os USING (order_id) WHERE l.scoring_eligible""",
    "seller_outcome_events": "SELECT seller_id, order_id, outcome_known_at, is_late, positive_delay_days FROM feature.seller_outcome_events",
    "seller_approval_events": "SELECT seller_id, order_id, approved_at FROM feature.seller_approval_events",
    "order_summary": """SELECT order_id, purchase_at, purchase_month, customer_region, label_eligible, is_late, item_value_brl
                        FROM mart.order_summary""",
    "order_categories": "SELECT order_id, category FROM mart.order_categories",
    "predictions": "SELECT run_id, model_name, order_id, block, prediction_date, score_raw, score_calibrated FROM evaluation.predictions",
}


def create_inputs(conn: psycopg.Connection, schema: str | None) -> dict[str, int]:
    """Copy benchmark inputs without indexes into `schema`, or into temporary tables if None."""
    counts = {}
    for name, query in INPUTS.items():
        target = f"{schema}.{name}" if schema else name
        kind = "TABLE" if schema else "TEMP TABLE"
        conn.execute(f"DROP TABLE IF EXISTS {target}")
        conn.execute(f"CREATE {kind} {target} AS {query}")  # noqa: S608 (fixed internal names)
        conn.execute(f"ANALYZE {target}")
        counts[name] = conn.execute(f"SELECT count(*) FROM {target}").fetchone()[0]  # noqa: S608
    return counts


@dataclass
class Variant:
    label: str
    sql_file: str
    setup: list[str] = field(default_factory=list)  # DDL run (and timed) before this variant


@dataclass
class Case:
    key: str
    title: str
    purpose: str
    params: dict | None
    variants: list[Variant]


def _plan_nodes(plan: dict) -> list[str]:
    out = []

    def walk(node, depth=0):
        label = node["Node Type"]
        if node.get("Relation Name"):
            label += f" on {node['Relation Name']}"
        if node.get("Index Name"):
            label += f" using {node['Index Name']}"
        out.append("  " * depth + label)
        for child in node.get("Plans", []):
            walk(child, depth + 1)

    walk(plan["Plan"])
    return out


def _explain(conn: psycopg.Connection, sql_text: str, params, timing: bool) -> dict:
    opts = "ANALYZE, BUFFERS, FORMAT JSON" + ("" if timing else ", TIMING OFF")
    with psycopg.ClientCursor(
        conn
    ) as cur:  # literal interpolation by psycopg (safe quoting) for EXPLAIN
        cur.execute(f"EXPLAIN ({opts}) {sql_text}", params)
        return cur.fetchone()[0][0]


def _run(conn: psycopg.Connection, sql_text: str, params) -> tuple[float, str, int]:
    t0 = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(sql_text, params)
        rows = cur.fetchall()
    elapsed = (time.perf_counter() - t0) * 1000
    digest = hashlib.md5(repr(rows).encode()).hexdigest()  # noqa: S324 (equality check, not security)
    return elapsed, digest, len(rows)


def _cases(conn: psycopg.Connection) -> list[Case]:
    run = conn.execute(
        "SELECT run_id FROM bench.predictions ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    day = conn.execute(
        "SELECT prediction_date FROM bench.predictions WHERE block = 'test' GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1"
    ).fetchone()
    top_cat = conn.execute(
        "SELECT category FROM bench.order_categories GROUP BY 1 ORDER BY count(*) DESC LIMIT 1"
    ).fetchone()
    cases = [
        Case(
            "Q1",
            "Seller history as-of (feature build)",
            "Point-in-time counts per (order, seller) — the most expensive feature query.",
            None,
            [
                Variant("before: LATERAL join, no index", "seller_history_lateral.sql"),
                Variant(
                    "rewrite: window functions over event streams, no index",
                    "seller_history_window.sql",
                ),
                Variant(
                    "after: LATERAL join + composite indexes",
                    "seller_history_lateral.sql",
                    [
                        "CREATE INDEX bench_outcome_asof ON bench.seller_outcome_events (seller_id, outcome_known_at) INCLUDE (is_late, positive_delay_days, order_id)",
                        "CREATE INDEX bench_approval_asof ON bench.seller_approval_events (seller_id, approved_at) INCLUDE (order_id)",
                    ],
                ),
            ],
        ),
        Case(
            "Q2",
            "Operational aggregation (overview page)",
            "Monthly KPIs of orders containing the most frequent category in the Southeast region.",
            {
                "start": datetime(2000, 1, 1),
                "end": datetime(2100, 1, 1),
                "regions": ["Southeast"],
                "categories": [top_cat[0]] if top_cat else ["none"],
            },
            [
                Variant("before: no index", "operational_monthly.sql"),
                Variant(
                    "after: index on order_categories (category, order_id)",
                    "operational_monthly.sql",
                    [
                        "CREATE INDEX bench_order_categories_cat ON bench.order_categories (category, order_id)",
                    ],
                ),
            ],
        ),
    ]
    if run and day:
        cases.append(
            Case(
                "Q3",
                "Filtered order extraction (risk review page)",
                "First page of one day's scored orders for one run and model, ranked by score.",
                {"run": run[0], "model": "lightgbm", "day": day[0]},
                [
                    Variant("before: no index", "risk_review_day.sql"),
                    Variant(
                        "after: composite index matching filter + order",
                        "risk_review_day.sql",
                        [
                            "CREATE INDEX bench_predictions_review ON bench.predictions "
                            "(run_id, model_name, prediction_date, score_raw DESC, order_id) INCLUDE (score_calibrated)",
                        ],
                    ),
                ],
            )
        )
    return cases


def _environment(conn: psycopg.Connection) -> dict:
    settings = dict(
        conn.execute(
            "SELECT name, setting || coalesce(' ' || unit, '') FROM pg_settings WHERE name IN "
            "('shared_buffers', 'work_mem', 'effective_cache_size', 'jit', 'max_parallel_workers_per_gather', 'random_page_cost')"
        ).fetchall()
    )
    env = {
        "postgres_version": conn.execute("SELECT version()").fetchone()[0],
        "settings": settings,
        "host_machine": platform.machine(),
        "host_os": platform.platform(),
        "host_cpu_count": os.cpu_count(),
    }
    try:
        info = subprocess.run(
            ["docker", "info", "--format", "{{.NCPU}} {{.MemTotal}}"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.split()
        env["docker_vm_cpus"], env["docker_vm_memory_gb"] = (
            int(info[0]),
            round(int(info[1]) / 1e9, 1),
        )
    except Exception:  # noqa: BLE001
        env["docker_vm_cpus"] = env["docker_vm_memory_gb"] = None
    return env


def _dash_timings(mode: str, repeats: int) -> list[dict]:
    """Callback rendering time (query + transfer + Python formatting), cold vs warm cache."""
    from delivery_risk.dashboard.data import DashboardData
    from delivery_risk.dashboard.pages import overview, risk_review

    data = DashboardData(mode)
    run = data.latest_run()
    calls = [
        (
            "overview (all filters off)",
            lambda: overview.render_overview(data, None, None, None, None),
        )
    ]
    if run:
        day = data.review_days(run["run_id"], run["selected_model"]).prediction_date.iloc[0]
        calls.append(
            (
                "risk review (one day, at approval)",
                lambda: risk_review.render_review(
                    data, run["selected_model"], day, 10, "approval", "risk_rank", "asc", 0
                ),
            )
        )
    out = []
    for label, fn in calls:
        cold, warm = [], []
        for _ in range(repeats):
            data._cache.clear()
            t0 = time.perf_counter()
            fn()
            cold.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            fn()
            warm.append((time.perf_counter() - t0) * 1000)
        out.append(
            {
                "callback": label,
                "cold_cache_ms_median": statistics.median(cold),
                "warm_cache_ms_median": statistics.median(warm),
            }
        )
    return out


def run_benchmark(mode: str, repeats: int = 5, timeout_s: int = 300) -> dict:
    out_dir = mode_config(mode).report_dir("sql_performance")
    plans_dir = out_dir / "plans"
    plans_dir.mkdir(exist_ok=True)
    results: dict = {
        "mode": mode,
        "synthetic": mode != "real",
        "measured_at": datetime.now(UTC).isoformat(),
        "repeats": repeats,
        "protocol": "1 warm-up execution, then N timed executions; caches not flushed (warm)",
        "cases": [],
    }
    with connect(mode, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS bench CASCADE")
        conn.execute("CREATE SCHEMA bench")
        results["input_rows"] = create_inputs(conn, "bench")
        results["environment"] = _environment(conn)
        conn.execute("SET search_path = bench, public")
        conn.execute(f"SET statement_timeout = '{int(timeout_s)}s'")
        for case in _cases(conn):
            case_out = {
                "key": case.key,
                "title": case.title,
                "purpose": case.purpose,
                "params": {k: str(v) for k, v in (case.params or {}).items()},
                "variants": [],
            }
            for i, variant in enumerate(case.variants):
                sql_text = (SQL_DIR / "benchmarks" / variant.sql_file).read_text()
                v = {"label": variant.label, "sql_file": variant.sql_file, "setup": []}
                for ddl in variant.setup:
                    t0 = time.perf_counter()
                    conn.execute(ddl)
                    build_ms = (time.perf_counter() - t0) * 1000
                    idx = ddl.split()[2]
                    size = conn.execute(
                        "SELECT pg_relation_size(%s)", (f"bench.{idx}",)
                    ).fetchone()[0]
                    conn.execute("ANALYZE " + ddl.split(" ON ")[1].split(" ")[0])
                    v["setup"].append(
                        {"ddl": ddl, "build_ms": round(build_ms, 1), "size_bytes": size}
                    )
                try:
                    plan = _explain(conn, sql_text, case.params, timing=True)
                    (plans_dir / f"{case.key}_{i}.json").write_text(json.dumps(plan, indent=1))
                    _run(conn, sql_text, case.params)  # warm-up
                    server, client, digests = [], [], set()
                    for _ in range(repeats):
                        server.append(
                            _explain(conn, sql_text, case.params, timing=False)["Execution Time"]
                        )
                        ms, digest, n_rows = _run(conn, sql_text, case.params)
                        client.append(ms)
                        digests.add(digest)
                    v.update(
                        {
                            "status": "ok",
                            "rows": n_rows,
                            "result_md5": digests.pop() if len(digests) == 1 else "UNSTABLE",
                            "server_ms_median": statistics.median(server),
                            "server_ms_min": min(server),
                            "server_ms_max": max(server),
                            "client_ms_median": statistics.median(client),
                            "client_ms_min": min(client),
                            "client_ms_max": max(client),
                            "plan_nodes": _plan_nodes(plan),
                            "shared_hit_blocks": plan["Plan"].get("Shared Hit Blocks"),
                            "shared_read_blocks": plan["Plan"].get("Shared Read Blocks"),
                            "planning_ms": plan.get("Planning Time"),
                        }
                    )
                except psycopg.errors.QueryCanceled:
                    v.update({"status": f"timeout after {timeout_s}s"})
                log.info(
                    "%s %-55s %s", case.key, variant.label, v.get("server_ms_median", v["status"])
                )
                case_out["variants"].append(v)
            md5s = {v.get("result_md5") for v in case_out["variants"] if v.get("status") == "ok"}
            case_out["results_identical"] = len(md5s) == 1
            results["cases"].append(case_out)
        conn.execute("RESET search_path")
    results["dash_callbacks"] = _dash_timings(mode, repeats)
    (out_dir / "benchmark_results.json").write_text(json.dumps(results, indent=2, default=str))
    _write_markdown(results, out_dir / "benchmark.md")
    log.info("Benchmark written to %s", out_dir)
    return results


def _write_markdown(r: dict, path) -> None:
    env = r["environment"]
    lines = [
        f"# SQL benchmark — {r['mode']}",
        "",
        "> **SYNTHETIC DATA** — timings describe the small synthetic test volume only.\n"
        if r["synthetic"]
        else "",
        f"Measured {r['measured_at'][:16]} UTC in the isolated `bench` schema. {r['protocol']}; N = {r['repeats']}.",
        "",
        "## Environment",
        "",
        md_table(
            ["item", "value"],
            [
                ("PostgreSQL", env["postgres_version"].split(" on ")[0]),
                ("Docker VM", f"{env['docker_vm_cpus']} CPUs, {env['docker_vm_memory_gb']} GB"),
                ("Host", f"{env['host_os']} ({env['host_machine']}, {env['host_cpu_count']} CPUs)"),
                *[(k, v) for k, v in env["settings"].items()],
            ],
        ),
        "## Input volumes (copied without indexes, then ANALYZEd)",
        "",
        md_table(["table", "rows"], list(r["input_rows"].items())),
    ]
    for case in r["cases"]:
        lines += [
            f"## {case['key']} — {case['title']}",
            "",
            case["purpose"],
            "",
            f"Parameters: `{case['params']}`" if case["params"] else "",
            "",
            md_table(
                [
                    "variant",
                    "status",
                    "server ms (median, min–max)",
                    "client ms: query + transfer (median)",
                    "rows",
                    "planning ms",
                ],
                [
                    (
                        v["label"],
                        v["status"],
                        f"{v['server_ms_median']:.1f} ({v['server_ms_min']:.1f}–{v['server_ms_max']:.1f})"
                        if v["status"] == "ok"
                        else "—",
                        f"{v['client_ms_median']:.1f}" if v["status"] == "ok" else "—",
                        v.get("rows"),
                        f"{v['planning_ms']:.2f}" if v.get("planning_ms") is not None else "—",
                    )
                    for v in case["variants"]
                ],
            ),
            f"Results identical across variants: **{'yes' if case['results_identical'] else 'NO'}**.",
            "",
        ]
        for v in case["variants"]:
            for s in v["setup"]:
                lines.append(
                    f"- Added by *{v['label']}*: `{s['ddl']}` — build {s['build_ms']:.0f} ms, size {s['size_bytes'] / 1024:.0f} KiB."
                )
            if v.get("plan_nodes"):
                lines += ["", f"Plan — *{v['label']}*:", "", "```", *v["plan_nodes"], "```", ""]
    lines += [
        "## Dash callback time (separate from query time)",
        "",
        "Rendering = queries + transfer + Python formatting of the page section; cold = cache cleared.",
        "",
        md_table(
            ["callback", "cold cache ms (median)", "warm cache ms (median)"],
            [
                (
                    d["callback"],
                    f"{d['cold_cache_ms_median']:.1f}",
                    f"{d['warm_cache_ms_median']:.1f}",
                )
                for d in r["dash_callbacks"]
            ],
        ),
    ]
    path.write_text("\n".join(lines))
