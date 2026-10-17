"""Deterministic SYNTHETIC dataset with the Olist file layout — used by the end-to-end tests only.

Purpose: exercise the full pipeline (ingestion -> SQL -> features -> models -> dashboard) in CI,
where the real files are not available (they are never committed). Every value is simulated from the seeded process below;
nothing is derived from the real dataset. Model performance measured on these files says
nothing about real marketplace behaviour.

The process plants a few realistic mechanisms so that every stage has something to show:
seller handling quality (some sellers degrade over time), distance-dependent transit, promise
padding, seasonal congestion, right censoring at a simulated export date, and the data-quality
quirks the pipeline must handle (duplicates, missing approvals, invalid timestamps, lost
leading zeros, outlier coordinates, multi-seller orders, multiple payments and reviews).
"""

from __future__ import annotations

import csv
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from delivery_risk.config import project_config
from delivery_risk.ingestion.contracts import CONTRACTS_BY_DATASET

log = logging.getLogger(__name__)

# state, region, customer weight, seller weight, centroid lat, lng, CEP prefix ranges
STATES: tuple[tuple[str, str, float, float, float, float, tuple[tuple[int, int], ...]], ...] = (
    ("SP", "Southeast", 0.420, 0.600, -23.55, -46.63, ((1000, 19999),)),
    ("RJ", "Southeast", 0.130, 0.055, -22.90, -43.20, ((20000, 28999),)),
    ("MG", "Southeast", 0.117, 0.080, -19.92, -43.94, ((30000, 39999),)),
    ("RS", "South", 0.055, 0.040, -30.03, -51.23, ((90000, 99999),)),
    ("PR", "South", 0.050, 0.075, -25.43, -49.27, ((80000, 87999),)),
    ("SC", "South", 0.036, 0.050, -27.59, -48.55, ((88000, 89999),)),
    ("BA", "Northeast", 0.034, 0.012, -12.97, -38.50, ((40000, 48999),)),
    ("DF", "Central-West", 0.021, 0.010, -15.79, -47.88, ((70000, 72799),)),
    ("ES", "Southeast", 0.020, 0.008, -20.32, -40.34, ((29000, 29999),)),
    ("GO", "Central-West", 0.020, 0.015, -16.68, -49.25, ((74000, 76799),)),
    ("PE", "Northeast", 0.017, 0.010, -8.05, -34.88, ((50000, 56999),)),
    ("CE", "Northeast", 0.013, 0.005, -3.73, -38.52, ((60000, 63999),)),
    ("PA", "North", 0.010, 0.002, -1.46, -48.50, ((66000, 68899),)),
    ("MT", "Central-West", 0.009, 0.004, -15.60, -56.10, ((78000, 78899),)),
    ("MA", "Northeast", 0.0075, 0.002, -2.53, -44.30, ((65000, 65999),)),
    ("MS", "Central-West", 0.0072, 0.004, -20.44, -54.65, ((79000, 79999),)),
    ("PB", "Northeast", 0.0054, 0.003, -7.12, -34.86, ((58000, 58999),)),
    ("PI", "Northeast", 0.0050, 0.002, -5.09, -42.80, ((64000, 64999),)),
    ("RN", "Northeast", 0.0050, 0.003, -5.79, -35.21, ((59000, 59999),)),
    ("AL", "Northeast", 0.0040, 0.002, -9.67, -35.74, ((57000, 57999),)),
    ("SE", "Northeast", 0.0034, 0.002, -10.91, -37.07, ((49000, 49999),)),
    ("TO", "North", 0.0028, 0.001, -10.18, -48.33, ((77000, 77999),)),
    ("RO", "North", 0.0025, 0.001, -8.76, -63.90, ((76800, 76999),)),
    ("AM", "North", 0.0015, 0.001, -3.12, -60.02, ((69000, 69299),)),
    ("AC", "North", 0.0008, 0.0005, -9.97, -67.81, ((69900, 69999),)),
    ("AP", "North", 0.0007, 0.0005, 0.03, -51.07, ((68900, 68999),)),
    ("RR", "North", 0.0005, 0.0005, 2.82, -60.67, ((69300, 69399),)),
)

# Portuguese category, English translation (None = absent from the translation file),
# median price BRL, median weight g, handling difficulty
CATEGORIES: tuple[tuple[str, str | None, float, float, float], ...] = (
    ("cama_mesa_banho", "bed_bath_table", 70, 1500, 0.0),
    ("beleza_saude", "health_beauty", 90, 400, -0.1),
    ("esporte_lazer", "sports_leisure", 95, 900, 0.0),
    ("moveis_decoracao", "furniture_decor", 110, 3000, 0.25),
    ("informatica_acessorios", "computers_accessories", 105, 600, 0.0),
    ("utilidades_domesticas", "housewares", 75, 1200, 0.05),
    ("relogios_presentes", "watches_gifts", 160, 350, -0.1),
    ("telefonia", "telephony", 60, 250, -0.05),
    ("ferramentas_jardim", "garden_tools", 85, 2500, 0.15),
    ("automotivo", "auto", 100, 1600, 0.1),
    ("brinquedos", "toys", 80, 900, 0.0),
    ("cool_stuff", "cool_stuff", 130, 1100, 0.05),
    ("perfumaria", "perfumery", 95, 450, -0.1),
    ("bebes", "baby", 100, 1300, 0.05),
    ("eletronicos", "electronics", 70, 500, 0.0),
    ("papelaria", "stationery", 50, 600, 0.0),
    ("moveis_escritorio", "office_furniture", 180, 9000, 0.45),
    ("eletrodomesticos", "home_appliances", 140, 4000, 0.2),
    ("pet_shop", "pet_shop", 70, 1500, 0.0),
    ("pc_gamer", None, 250, 2500, 0.1),
)

TS_FMT = "%Y-%m-%d %H:%M:%S"


@dataclass
class Seller:
    seller_id: str
    state_idx: int
    zip_prefix: int
    handling_median_days: float
    degrades_on: date | None
    activity: float


def _hex_id(rng: np.random.Generator) -> str:
    return "".join(rng.choice(list("0123456789abcdef"), size=32))


def _haversine(lat1, lng1, lat2, lng2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


class _Geo:
    """Prefix -> location; each prefix gets a stable offset around its state centroid."""

    def __init__(self, rng: np.random.Generator):
        self.rng = rng
        self.loc: dict[int, tuple[float, float, int]] = {}

    def random_prefix(self, state_idx: int) -> int:
        lo, hi = STATES[state_idx][6][0]
        # concentrate on a limited number of prefixes per state (realistic reuse)
        n_buckets = 60
        bucket = int(self.rng.integers(0, n_buckets))
        prefix = lo + (hi - lo) * bucket // n_buckets + int(self.rng.integers(0, 5))
        if prefix not in self.loc:
            _, _, _, _, lat, lng, _ = STATES[state_idx]
            spread = 1.2 if STATES[state_idx][0] not in ("DF",) else 0.3
            # keep simulated points on land-ish coordinates (coastal centroids + noise would
            # otherwise fall in the Atlantic, outside the Brazil bounding box)
            plat = min(4.5, max(-33.0, lat + self.rng.normal(0, spread)))
            plng = min(-35.0, max(-73.0, lng + self.rng.normal(0, spread)))
            self.loc[prefix] = (plat, plng, state_idx)
        return prefix

    def position(self, prefix: int) -> tuple[float, float]:
        lat, lng, _ = self.loc[prefix]
        return lat, lng


def _season_factor(day: date) -> float:
    """Simulated congestion: Black Friday / year-end and a February-March disruption."""
    if date(2017, 11, 20) <= day <= date(2017, 12, 22):
        return 1.55
    if date(2018, 2, 10) <= day <= date(2018, 3, 25):
        return 1.45
    return 1.0


def _daily_weights(start: date, end: date) -> tuple[list[date], np.ndarray]:
    days = [start + timedelta(d) for d in range((end - start).days + 1)]
    w = []
    for i, d in enumerate(days):
        growth = 0.45 + 1.1 * i / len(days)
        weekly = 1.1 if d.weekday() < 5 else 0.8
        spike = (
            4.0
            if d == date(2017, 11, 24)
            else (1.6 if date(2017, 11, 20) <= d <= date(2017, 11, 30) else 1.0)
        )
        w.append(growth * weekly * spike)
    arr = np.array(w)
    return days, arr / arr.sum()


def generate(
    seed: int,
    n_orders: int,
    n_sellers: int,
    n_products: int,
    start: date,
    end: date,
    export_cutoff: datetime,
) -> dict[str, list[list[str]]]:
    rng = np.random.default_rng(seed)
    geo = _Geo(rng)
    cust_w = np.array([s[2] for s in STATES])
    cust_w /= cust_w.sum()
    sell_w = np.array([s[3] for s in STATES])
    sell_w /= sell_w.sum()

    # ---------------------------------------------------------------- sellers
    sellers: list[Seller] = []
    for _ in range(n_sellers):
        st = int(rng.choice(len(STATES), p=sell_w))
        struggling = rng.random() < 0.12
        handling = float(np.exp(rng.normal(np.log(4.5 if struggling else 1.8), 0.35)))
        degrades = None
        if rng.random() < 0.08:
            degrades = start + timedelta(days=int(rng.integers(200, (end - start).days - 60)))
        sellers.append(
            Seller(
                _hex_id(rng),
                st,
                geo.random_prefix(st),
                handling,
                degrades,
                float(rng.pareto(1.3) + 0.2),
            )
        )
    act = np.array([s.activity for s in sellers])
    act /= act.sum()

    # ---------------------------------------------------------------- products (each owned by one seller)
    cat_w = rng.dirichlet(np.ones(len(CATEGORIES)) * 2)
    products = []
    for i in range(n_products):
        c = int(rng.choice(len(CATEGORIES), p=cat_w))
        _, _, price_med, weight_med, _ = CATEGORIES[c]
        seller_idx = int(rng.choice(n_sellers, p=act)) if i >= n_sellers else i
        weight = max(50, round(float(np.exp(rng.normal(np.log(weight_med), 0.6)))))
        dims = [max(5, round(float(np.exp(rng.normal(np.log(18), 0.4))))) for _ in range(3)]
        products.append(
            {
                "product_id": _hex_id(rng),
                "cat": c,
                "seller": seller_idx,
                "price_med": price_med * float(np.exp(rng.normal(0, 0.5))),
                "weight": weight,
                "dims": dims,
                "missing_cat": rng.random() < 0.015,
                "name_len": int(rng.integers(20, 64)),
                "desc_len": int(rng.integers(80, 2500)),
                "photos": int(rng.integers(1, 7)),
            }
        )
    by_seller: dict[int, list[int]] = {}
    for idx, p in enumerate(products):
        by_seller.setdefault(p["seller"], []).append(idx)

    # ---------------------------------------------------------------- orders
    days, day_p = _daily_weights(start, end)
    order_days = np.sort(rng.choice(len(days), size=n_orders, p=day_p))
    rows = {k: [] for k in CONTRACTS_BY_DATASET}
    unique_ids: list[tuple[str, int, int]] = []  # (customer_unique_id, state, prefix)
    review_ids_by_unique: dict[str, list[str]] = {}

    for n, day_idx in enumerate(order_days):
        d = days[day_idx]
        purchase = datetime(d.year, d.month, d.day) + timedelta(minutes=int(rng.integers(0, 1440)))
        order_id = _hex_id(rng)
        customer_id = _hex_id(rng)
        if unique_ids and rng.random() < 0.03:
            uid, cst, cprefix = unique_ids[int(rng.integers(0, len(unique_ids)))]
        else:
            cst = int(rng.choice(len(STATES), p=cust_w))
            uid, cprefix = _hex_id(rng), geo.random_prefix(cst)
            unique_ids.append((uid, cst, cprefix))
        clat, clng = geo.position(cprefix)

        # approval (boleto-like payments approve later)
        boleto = rng.random() < 0.19
        approval_delay_h = rng.exponential(30.0) if boleto else rng.exponential(0.35)
        approved = purchase + timedelta(hours=float(approval_delay_h))
        missing_approval = rng.random() < 0.003

        # items
        n_items = int(rng.choice([1, 2, 3, 4], p=[0.88, 0.09, 0.02, 0.01]))
        s0 = int(rng.choice(n_sellers, p=act))
        if s0 not in by_seller:
            by_seller[s0] = [int(rng.integers(0, n_products))]
        item_list = []
        first_product = int(rng.choice(by_seller[s0]))
        for k in range(n_items):
            if k == 0:
                s_idx, p_idx = s0, first_product
            else:
                r = rng.random()
                if r < 0.72:
                    s_idx, p_idx = s0, first_product
                elif r < 0.88:
                    s_idx, p_idx = s0, int(rng.choice(by_seller[s0]))
                else:
                    s_idx = int(rng.choice(n_sellers, p=act))
                    by_seller.setdefault(s_idx, [int(rng.integers(0, n_products))])
                    p_idx = int(rng.choice(by_seller[s_idx]))
            item_list.append((s_idx, p_idx))

        # distances and promise
        dist = max(
            _haversine(clat, clng, *geo.position(sellers[s].zip_prefix)) for s, _ in item_list
        )
        weight_kg = sum(products[p]["weight"] for _, p in item_list) / 1000
        expected_days = 1.0 + 2.2 + 2.0 + 0.0055 * dist
        promised_lead = max(4, int(math.ceil(expected_days * 1.45 + 3 + rng.normal(0, 2.0))))
        promised_date = d + timedelta(days=promised_lead)

        # realised timeline
        season = _season_factor(d)
        handling = 0.0
        for s, p in item_list:
            sel = sellers[s]
            med = sel.handling_median_days * (
                2.3 if sel.degrades_on and d >= sel.degrades_on else 1.0
            )
            med *= 1 + CATEGORIES[products[p]["cat"]][4]
            handling = max(handling, float(np.exp(rng.normal(np.log(med), 0.55))))
        if len({s for s, _ in item_list}) > 1:
            handling *= 1.25
        carrier = approved + timedelta(days=handling * (1 + 0.3 * (season - 1)))
        transit = (1.5 + 0.0055 * dist + 0.25 * weight_kg) * season * float(rng.gamma(4.0, 0.25))
        if rng.random() < 0.02:  # lost / re-routed parcel
            transit += float(rng.uniform(7, 25))
        delivered = carrier + timedelta(days=transit)
        delivered = delivered.replace(
            hour=int(rng.integers(8, 21)), minute=int(rng.integers(0, 60))
        )
        if delivered <= carrier:
            delivered = carrier + timedelta(hours=6)

        # status (right censoring at the export date)
        r = rng.random()
        canceled = r < 0.008
        unavailable = 0.008 <= r < 0.013
        if canceled:
            status = "canceled"
        elif unavailable:
            status = "unavailable"
        elif delivered <= export_cutoff:
            status = "delivered"
        elif carrier <= export_cutoff:
            status = "shipped"
        elif approved <= export_cutoff:
            status = "invoiced"
        else:
            status = "processing"

        ts = lambda x: x.strftime(TS_FMT) if x is not None else ""  # noqa: E731
        approved_txt = "" if missing_approval else ts(approved)
        carrier_txt = ts(carrier) if status in ("delivered", "shipped") else ""
        delivered_txt = ts(delivered) if status == "delivered" else ""
        purchase_txt = ts(purchase)
        # planted anomalies
        if status == "canceled" and rng.random() < 0.06 and delivered <= export_cutoff:
            delivered_txt = ts(delivered)  # canceled with a delivery date
        if status == "delivered" and rng.random() < 0.0006:
            delivered_txt = ""  # delivered without date
        if carrier_txt and rng.random() < 0.004:
            carrier_txt = ts(approved - timedelta(hours=float(rng.uniform(1, 30))))
        if n in (777, 4321) and status == "delivered":
            delivered_txt = ts(approved - timedelta(hours=3))  # delivered before approval
        if n == 2024:
            purchase_txt = f"{d.year}-02-30 10:00:00"  # invalid calendar date -> quarantine
        order_row = [
            order_id,
            customer_id,
            status,
            purchase_txt,
            approved_txt,
            carrier_txt,
            delivered_txt,
            promised_date.strftime("%Y-%m-%d 00:00:00"),
        ]
        rows["orders"].append(order_row)
        if n % 997 == 0:
            rows["orders"].append(list(order_row))  # strict duplicate

        # customer (zip with leading zeros for half the rows, raw integer-like otherwise)
        zip_txt = f"{cprefix:05d}" if rng.random() < 0.5 else str(cprefix)
        rows["customers"].append(
            [customer_id, uid, zip_txt, f"city_{STATES[cst][0].lower()}", STATES[cst][0]]
        )

        # items, freight
        total = 0.0
        shipping_limit = (approved if not missing_approval else purchase) + timedelta(
            days=float(sellers[s0].handling_median_days) + 2
        )
        for k, (s, p) in enumerate(item_list, start=1):
            prod = products[p]
            price = round(max(4.9, prod["price_med"] * float(np.exp(rng.normal(0, 0.08)))), 2)
            freight = round(
                max(0.0, 8 + 0.006 * dist + 2.2 * prod["weight"] / 1000 + rng.normal(0, 2)), 2
            )
            total += price + freight
            rows["order_items"].append(
                [
                    order_id,
                    str(k),
                    prod["product_id"],
                    sellers[s].seller_id,
                    shipping_limit.strftime(TS_FMT),
                    f"{price:.2f}",
                    f"{freight:.2f}",
                ]
            )

        # payments
        if rng.random() > 0.0003:
            total_r = round(total + (rng.choice([-2.0, 1.5]) if rng.random() < 0.004 else 0.0), 2)
            ptype = (
                "boleto"
                if boleto
                else str(rng.choice(["credit_card", "credit_card", "credit_card", "debit_card"]))
            )
            if rng.random() < 0.04 and total_r > 20:
                voucher = round(total_r * float(rng.uniform(0.1, 0.5)), 2)
                parts = [(ptype, total_r - voucher), ("voucher", voucher)]
            else:
                parts = [(ptype, total_r)]
            for seq, (pt, val) in enumerate(parts, start=1):
                inst = int(rng.integers(1, 11)) if pt == "credit_card" else 1
                rows["payments"].append([order_id, str(seq), pt, str(inst), f"{val:.2f}"])

        # reviews (score depends on lateness; created after delivery or estimated date)
        if rng.random() < 0.97:
            late = status == "delivered" and delivered.date() > promised_date
            base = rng.choice(
                [1, 2, 3, 4, 5],
                p=[0.45, 0.15, 0.15, 0.15, 0.10] if late else [0.06, 0.03, 0.08, 0.2, 0.63],
            )
            created = (
                delivered
                if status == "delivered"
                else datetime.combine(promised_date, datetime.min.time())
            ) + timedelta(days=1)
            msg = (
                ""
                if rng.random() < 0.6
                else str(
                    rng.choice(
                        [
                            "Produto otimo, recomendo",
                            "Chegou antes do prazo",
                            "Atrasou, mas chegou,\nproduto ok",
                            "Nao recebi ainda",
                        ]
                    )
                )
            )
            n_rev = 2 if rng.random() < 0.01 else 1
            for j in range(n_rev):
                rid = _hex_id(rng)
                review_ids_by_unique.setdefault(uid, []).append(rid)
                rows["reviews"].append(
                    [
                        rid,
                        order_id,
                        str(int(base) if j == 0 else int(rng.integers(1, 6))),
                        "",
                        msg,
                        created.strftime("%Y-%m-%d 00:00:00"),
                        (created + timedelta(hours=float(rng.uniform(5, 60)))).strftime(TS_FMT),
                    ]
                )

    # a few review ids shared by two orders of the same returning customer
    shared = 0
    for rids in review_ids_by_unique.values():
        if len(rids) >= 2 and shared < 12:
            src = next(r for r in rows["reviews"] if r[0] == rids[0])
            other = next(r for r in rows["reviews"] if r[0] == rids[-1])
            if src[1] != other[1]:
                rows["reviews"].append([src[0], other[1], src[2], "", "", other[5], other[6]])
                shared += 1

    # ---------------------------------------------------------------- dimension files
    for s in sellers:
        zip_txt = f"{s.zip_prefix:05d}" if rng.random() < 0.6 else str(s.zip_prefix)
        rows["sellers"].append(
            [s.seller_id, zip_txt, f"city_{STATES[s.state_idx][0].lower()}", STATES[s.state_idx][0]]
        )
    for p in products:
        cat = "" if p["missing_cat"] else CATEGORIES[p["cat"]][0]
        dims = p["dims"]
        rows["products"].append(
            [
                p["product_id"],
                cat,
                "" if p["missing_cat"] else str(p["name_len"]),
                "" if p["missing_cat"] else str(p["desc_len"]),
                "" if p["missing_cat"] else str(p["photos"]),
                str(p["weight"]),
                str(dims[0]),
                str(dims[1]),
                str(dims[2]),
            ]
        )
    for pt, en, *_ in CATEGORIES:
        if en is not None:
            rows["category_translation"].append([pt, en])
    for prefix, (lat, lng, st) in sorted(geo.loc.items()):
        for _ in range(int(rng.integers(1, 6))):
            plat, plng = lat + rng.normal(0, 0.01), lng + rng.normal(0, 0.01)
            row = [
                str(prefix),
                f"{plat:.6f}",
                f"{plng:.6f}",
                f"city_{STATES[st][0].lower()}",
                STATES[st][0],
            ]
            rows["geolocation"].append(row)
            if rng.random() < 0.25:
                rows["geolocation"].append(list(row))  # exact duplicate
        if rng.random() < 0.01:
            rows["geolocation"].append(
                [str(prefix), f"{-lng:.6f}", f"{lat:.6f}", "outlier", STATES[st][0]]
            )
    return rows


def generate_to_directory(directory: Path, n_orders: int | None = None) -> dict[str, int]:
    cfg = project_config()["synthetic"]
    rows = generate(
        seed=cfg["seed"],
        n_orders=n_orders or cfg["n_orders"],
        n_sellers=cfg["n_sellers"],
        n_products=cfg["n_products"],
        start=date.fromisoformat(cfg["start_date"]),
        end=date.fromisoformat(cfg["end_date"]),
        export_cutoff=datetime.fromisoformat(cfg["export_cutoff"]),
    )
    directory.mkdir(parents=True, exist_ok=True)
    counts = {}
    for dataset, data in rows.items():
        contract = CONTRACTS_BY_DATASET[dataset]
        with open(directory / contract.file_name, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(contract.columns)
            writer.writerows(data)
        counts[dataset] = len(data)
    (directory / "SYNTHETIC_DATA_README.txt").write_text(
        "SYNTHETIC DATA generated by delivery_risk.ingestion.synthetic (seeded).\n"
        "Not derived from the real Olist dataset. Do not report results on it as real findings.\n"
    )
    log.info("Synthetic dataset written to %s: %s", directory, counts)
    return counts
