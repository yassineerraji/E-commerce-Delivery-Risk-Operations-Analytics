"""Command-line interface: `uv run delivery-risk <command> [--mode real]`.

Every step is explicit; nothing downloads data implicitly. See README.md for the full flow.
"""

from __future__ import annotations

import argparse
import logging
import secrets
import sys
from datetime import UTC, datetime

from delivery_risk.config import MODES, PROJECT_ROOT, mode_config
from delivery_risk.logs import setup_logging

log = logging.getLogger("delivery_risk")


def cmd_setup_env(_: argparse.Namespace) -> None:
    """Create a local .env with random passwords (never overwrites an existing file)."""
    target = PROJECT_ROOT / ".env"
    if target.exists():
        log.info(".env already exists; leaving it unchanged")
        return
    lines = []
    for line in (PROJECT_ROOT / ".env.example").read_text().splitlines():
        if line.endswith("PASSWORD=change-me"):
            line = line.replace("change-me", secrets.token_urlsafe(18))
        lines.append(line)
    target.write_text("\n".join(lines) + "\n")
    target.chmod(0o600)
    log.info("Created .env with random local passwords (git-ignored)")


def cmd_db_init(args: argparse.Namespace) -> None:
    from delivery_risk.db import init_databases

    init_databases(tuple(args.modes))


def cmd_db_reset(args: argparse.Namespace) -> None:
    from delivery_risk.db import reset_mode

    if not args.yes:
        raise SystemExit(
            "Refusing to reset without --yes (this drops the project schemas of one mode)."
        )
    reset_mode(args.mode)


def cmd_ingest(args: argparse.Namespace) -> None:
    from delivery_risk.ingestion.load_raw import ingest_directory

    acquired = (
        datetime.fromisoformat(args.acquired_on).replace(tzinfo=UTC) if args.acquired_on else None
    )
    raw_dir = mode_config(args.mode).raw_dir
    ingest_directory(args.mode, raw_dir, source_version=args.source_version, acquired_at=acquired)


def cmd_transform(args: argparse.Namespace) -> None:
    from delivery_risk.pipeline import run_transform

    run_transform(args.mode)


def cmd_quality(args: argparse.Namespace) -> None:
    from delivery_risk.quality import write_quality_reports

    write_quality_reports(args.mode)


def cmd_features(args: argparse.Namespace) -> None:
    from delivery_risk.pipeline import run_features

    run_features(args.mode)


def cmd_train(args: argparse.Namespace) -> None:
    from delivery_risk.modeling.train import train

    train(args.mode)


def cmd_evaluate(args: argparse.Namespace) -> None:
    from delivery_risk.evaluation.evaluate import evaluate

    evaluate(args.mode, run_id=args.run_id)


def cmd_benchmark(args: argparse.Namespace) -> None:
    from delivery_risk.benchmark import run_benchmark

    run_benchmark(args.mode, repeats=args.repeats)


def cmd_report(args: argparse.Namespace) -> None:
    from delivery_risk.reporting import build_reports

    build_reports(args.mode)


def cmd_final_report(args: argparse.Namespace) -> None:
    from delivery_risk.final_report import build

    build(args.mode, compile_pdf=not args.no_pdf)


def cmd_dashboard(args: argparse.Namespace) -> None:
    from delivery_risk.dashboard.app import create_app

    app = create_app(args.mode)
    app.run(host=args.host, port=args.port, debug=False)


def cmd_screenshots(args: argparse.Namespace) -> None:
    from delivery_risk.screenshots import capture

    capture(args.mode, base_url=args.base_url)


def cmd_reproduce(args: argparse.Namespace) -> None:
    """Real-data pipeline end to end (requires files already present in data/raw/olist)."""
    for step in (
        cmd_ingest,
        cmd_transform,
        cmd_quality,
        cmd_features,
        cmd_train,
        cmd_evaluate,
        cmd_report,
    ):
        log.info("=== reproduce step: %s", step.__name__.removeprefix("cmd_"))
        step(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="delivery-risk", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func, help_text: str, mode: bool = True, default_mode: str = "real"):
        p = sub.add_parser(name, help=help_text)
        if mode:
            p.add_argument("--mode", choices=MODES, default=default_mode)
        p.set_defaults(func=func)
        return p

    add("setup-env", cmd_setup_env, "create a local .env with random passwords", mode=False)
    p = add("db-init", cmd_db_init, "create roles, databases, schemas (idempotent)", mode=False)
    p.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    p = add(
        "db-reset", cmd_db_reset, "DESTRUCTIVE: drop and recreate the project schemas of one mode"
    )
    p.add_argument("--yes", action="store_true")
    p = add("ingest", cmd_ingest, "load source CSV files into raw tables with a manifest")
    p.add_argument(
        "--source-version", default=None, help="dataset version shown on the source page"
    )
    p.add_argument(
        "--acquired-on", default=None, help="acquisition date YYYY-MM-DD (default: file mtime)"
    )
    add("transform", cmd_transform, "staging -> core -> marts in one checked transaction")
    add("quality", cmd_quality, "data-quality, coverage and censoring reports")
    add("features", cmd_features, "point-in-time feature matrix")
    add("train", cmd_train, "model selection, refit and calibration (never touches the test block)")
    p = add("evaluate", cmd_evaluate, "final test evaluation of a frozen run")
    p.add_argument("--run-id", default=None)
    p = add("benchmark", cmd_benchmark, "SQL benchmark in an isolated schema")
    p.add_argument("--repeats", type=int, default=5)
    add("report", cmd_report, "figures and evaluation report from saved artifacts")
    p = add(
        "final-report", cmd_final_report, "final PDF report (figures, tables, LaTeX via tectonic)"
    )
    p.add_argument("--no-pdf", action="store_true", help="generate figures and tables only")
    p = add("dashboard", cmd_dashboard, "run the Dash application locally")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8050)
    p = add(
        "screenshots", cmd_screenshots, "capture dashboard screenshots (dashboard must be running)"
    )
    p.add_argument("--base-url", default="http://127.0.0.1:8050")
    p = add("reproduce", cmd_reproduce, "real-data pipeline end to end (no download)", mode=False)
    p.set_defaults(mode="real", source_version=None, acquired_on=None, run_id=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:  # readable failure, no stack trace with connection details by default
        log.error("%s: %s", type(exc).__name__, exc)
        if "--debug" in sys.argv or logging.getLogger().isEnabledFor(logging.DEBUG):
            raise
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
