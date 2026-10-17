"""Timestamped, secret-free logging setup shared by the CLI and the dashboard."""

from __future__ import annotations

import logging
import re

_SECRET_PATTERN = re.compile(r"(password=)\S+", re.IGNORECASE)


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _SECRET_PATTERN.sub(r"\1***", record.msg)
        return True


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    handler.addFilter(_RedactingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("matplotlib", "PIL", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
