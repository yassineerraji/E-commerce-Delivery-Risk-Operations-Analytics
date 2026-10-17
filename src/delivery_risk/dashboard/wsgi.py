"""WSGI entry point for gunicorn: `gunicorn delivery_risk.dashboard.wsgi:server`."""

import os

from delivery_risk.dashboard.app import create_app
from delivery_risk.logs import setup_logging

setup_logging()
app = create_app(os.environ.get("DR_MODE", "real"))
server = app.server
