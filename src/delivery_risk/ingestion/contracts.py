"""Expected source files and their header contracts.

The column names reproduce the public Olist files exactly, including the original
misspellings (`product_name_lenght`). Headers are checked at import time; a missing or
unexpected column stops the load instead of being silently patched.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FileContract:
    dataset: str  # logical name, also the raw table name (raw.<dataset>)
    file_name: str
    columns: tuple[str, ...]
    candidate_key: tuple[str, ...] | None  # grain to verify, not to assume
    role: str


CONTRACTS: tuple[FileContract, ...] = (
    FileContract(
        "orders",
        "olist_orders_dataset.csv",
        (
            "order_id",
            "customer_id",
            "order_status",
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ),
        ("order_id",),
        "Orders, statuses and lifecycle timestamps",
    ),
    FileContract(
        "order_items",
        "olist_order_items_dataset.csv",
        (
            "order_id",
            "order_item_id",
            "product_id",
            "seller_id",
            "shipping_limit_date",
            "price",
            "freight_value",
        ),
        ("order_id", "order_item_id"),
        "Items, sellers, prices and freight",
    ),
    FileContract(
        "customers",
        "olist_customers_dataset.csv",
        (
            "customer_id",
            "customer_unique_id",
            "customer_zip_code_prefix",
            "customer_city",
            "customer_state",
        ),
        ("customer_id",),
        "Order-level customer key and repeat-customer identifier",
    ),
    FileContract(
        "sellers",
        "olist_sellers_dataset.csv",
        ("seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"),
        ("seller_id",),
        "Sellers",
    ),
    FileContract(
        "products",
        "olist_products_dataset.csv",
        (
            "product_id",
            "product_category_name",
            "product_name_lenght",
            "product_description_lenght",
            "product_photos_qty",
            "product_weight_g",
            "product_length_cm",
            "product_height_cm",
            "product_width_cm",
        ),
        ("product_id",),
        "Product attributes (final snapshot)",
    ),
    FileContract(
        "payments",
        "olist_order_payments_dataset.csv",
        ("order_id", "payment_sequential", "payment_type", "payment_installments", "payment_value"),
        ("order_id", "payment_sequential"),
        "Payments",
    ),
    FileContract(
        "reviews",
        "olist_order_reviews_dataset.csv",
        (
            "review_id",
            "order_id",
            "review_score",
            "review_comment_title",
            "review_comment_message",
            "review_creation_date",
            "review_answer_timestamp",
        ),
        ("review_id", "order_id"),
        "Customer reviews (neither order_id nor review_id assumed unique)",
    ),
    FileContract(
        "geolocation",
        "olist_geolocation_dataset.csv",
        (
            "geolocation_zip_code_prefix",
            "geolocation_lat",
            "geolocation_lng",
            "geolocation_city",
            "geolocation_state",
        ),
        None,
        "Coordinates by zip-code prefix (several rows per prefix)",
    ),
    FileContract(
        "category_translation",
        "product_category_name_translation.csv",
        ("product_category_name", "product_category_name_english"),
        ("product_category_name",),
        "Category name translation (Portuguese to English)",
    ),
)

CONTRACTS_BY_DATASET = {c.dataset: c for c in CONTRACTS}
