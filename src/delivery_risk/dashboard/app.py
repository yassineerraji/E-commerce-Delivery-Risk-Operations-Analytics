"""Dash application factory: `create_app(mode)`.

Local, read-only analytics tool for a retrospective study. Not a production or real-time system.
"""

from __future__ import annotations

from pathlib import Path

from dash import Dash, Input, Output, dcc, html

from delivery_risk.dashboard.data import DashboardData
from delivery_risk.dashboard.pages import model_quality, overview, risk_review

PAGES = {
    "/": ("Operations overview", overview),
    "/risk-review": ("Historical risk review", risk_review),
    "/model-quality": ("Model & data quality", model_quality),
}


def header(mode: str) -> html.Header:
    badge_cls = "mode-badge" + (" synthetic" if mode != "real" else "")
    return html.Header(
        [
            html.Div(
                [
                    html.H1(
                        "E-commerce Delivery Risk — Historical Analytics", className="app-title"
                    ),
                    html.Div(
                        "Retrospective study on the public Olist dataset layout · PostgreSQL + SQL + ML",
                        className="app-subtitle",
                    ),
                ]
            ),
            html.Nav(
                [
                    dcc.Link(label, href=path, id=f"nav-{i}")
                    for i, (path, (label, _)) in enumerate(PAGES.items())
                ],
                className="nav",
            ),
            html.Span(
                f"mode: {mode}{' · synthetic' if mode != 'real' else ''}", className=badge_cls
            ),
        ],
        className="app-header",
    )


def footer() -> html.Footer:
    """Data attribution required by the CC BY-NC-SA 4.0 licence of the source dataset."""
    return html.Footer(
        [
            "Data: ",
            html.A(
                "Brazilian E-Commerce Public Dataset by Olist",
                href="https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce",
                target="_blank",
                rel="noopener",
            ),
            " (Kaggle), licensed ",
            html.A(
                "CC BY-NC-SA 4.0",
                href="https://creativecommons.org/licenses/by-nc-sa/4.0/",
                target="_blank",
                rel="noopener",
            ),
            ". Adapted into analytical tables, features and historical risk scores. "
            "Retrospective, non-commercial analysis; not "
            "affiliated with Olist. ",
            html.A(
                "Source code",
                href="https://github.com/yassineerraji/E-commerce-Delivery-Risk-Operations-Analytics",
                target="_blank",
                rel="noopener",
            ),
            ".",
        ],
        className="app-footer",
    )


def create_app(mode: str) -> Dash:
    data = DashboardData(mode)
    app = Dash(
        __name__,
        title="Delivery Risk Analytics",
        suppress_callback_exceptions=True,
        assets_folder=str(Path(__file__).parent / "assets"),
    )
    app.layout = html.Div(
        [dcc.Location(id="url"), header(mode), html.Main(id="page", className="page"), footer()]
    )

    @app.server.route("/health")
    def health():  # liveness probe for the hosting platform (does not query the database)
        return {"status": "ok"}

    for _, module in PAGES.values():
        module.register(app, data)

    @app.callback(
        Output("page", "children"),
        *[Output(f"nav-{i}", "className") for i in range(len(PAGES))],
        Input("url", "pathname"),
    )
    def route(pathname):
        path = pathname if pathname in PAGES else "/"
        classes = ["active" if p == path else "" for p in PAGES]
        return PAGES[path][1].layout(data, mode), *classes

    app.server.config["DR_DATA"] = data
    return app
