"""Capture dashboard screenshots with a local headless Chrome/Chromium (no extra dependency)."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from delivery_risk.config import PROJECT_ROOT

log = logging.getLogger(__name__)
PAGES = {"overview": "", "risk-review": "risk-review", "model-quality": "model-quality"}
WIDTHS = (1440, 1024)
CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
    "chromium-browser",
)


def find_browser() -> str | None:
    for c in CANDIDATES:
        if Path(c).exists() or shutil.which(c):
            return c
    return None


def capture(mode: str, base_url: str = "http://127.0.0.1:8050", height: int = 2600) -> list[Path]:
    browser = find_browser()
    if browser is None:
        raise RuntimeError(
            "No Chrome/Chromium found: screenshot validation remains to be done manually."
        )
    out_dir = PROJECT_ROOT / "reports" / "screenshots" / mode
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, path in PAGES.items():
        for width in WIDTHS:
            target = out_dir / f"{name}_{width}.png"
            subprocess.run(
                [
                    browser,
                    "--headless=new",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    f"--window-size={width},{height}",
                    "--virtual-time-budget=15000",
                    f"--screenshot={target}",
                    f"{base_url}/{path}",
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
            paths.append(target)
            log.info("Screenshot %s", target.relative_to(PROJECT_ROOT))
    return paths
