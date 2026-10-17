-- 002_raw.sql — immutable text copies of the source files.
-- Every column is text so that typing errors can be audited in staging.
-- load_id/source_file/source_line identify the batch and the physical line where the record starts.
-- parse_error is set when a CSV record does not have the expected number of fields.

CREATE TABLE IF NOT EXISTS raw.orders (
    load_id bigint NOT NULL REFERENCES meta.load_manifest (load_id),
    source_file text NOT NULL,
    source_line integer NOT NULL,
    parse_error text,
    order_id text, customer_id text, order_status text,
    order_purchase_timestamp text, order_approved_at text,
    order_delivered_carrier_date text, order_delivered_customer_date text,
    order_estimated_delivery_date text
);

CREATE TABLE IF NOT EXISTS raw.order_items (
    load_id bigint NOT NULL REFERENCES meta.load_manifest (load_id),
    source_file text NOT NULL,
    source_line integer NOT NULL,
    parse_error text,
    order_id text, order_item_id text, product_id text, seller_id text,
    shipping_limit_date text, price text, freight_value text
);

CREATE TABLE IF NOT EXISTS raw.customers (
    load_id bigint NOT NULL REFERENCES meta.load_manifest (load_id),
    source_file text NOT NULL,
    source_line integer NOT NULL,
    parse_error text,
    customer_id text, customer_unique_id text, customer_zip_code_prefix text,
    customer_city text, customer_state text
);

CREATE TABLE IF NOT EXISTS raw.sellers (
    load_id bigint NOT NULL REFERENCES meta.load_manifest (load_id),
    source_file text NOT NULL,
    source_line integer NOT NULL,
    parse_error text,
    seller_id text, seller_zip_code_prefix text, seller_city text, seller_state text
);

CREATE TABLE IF NOT EXISTS raw.products (
    load_id bigint NOT NULL REFERENCES meta.load_manifest (load_id),
    source_file text NOT NULL,
    source_line integer NOT NULL,
    parse_error text,
    product_id text, product_category_name text,
    product_name_lenght text, product_description_lenght text, product_photos_qty text,
    product_weight_g text, product_length_cm text, product_height_cm text, product_width_cm text
);

CREATE TABLE IF NOT EXISTS raw.payments (
    load_id bigint NOT NULL REFERENCES meta.load_manifest (load_id),
    source_file text NOT NULL,
    source_line integer NOT NULL,
    parse_error text,
    order_id text, payment_sequential text, payment_type text,
    payment_installments text, payment_value text
);

CREATE TABLE IF NOT EXISTS raw.reviews (
    load_id bigint NOT NULL REFERENCES meta.load_manifest (load_id),
    source_file text NOT NULL,
    source_line integer NOT NULL,
    parse_error text,
    review_id text, order_id text, review_score text,
    review_comment_title text, review_comment_message text,
    review_creation_date text, review_answer_timestamp text
);

CREATE TABLE IF NOT EXISTS raw.geolocation (
    load_id bigint NOT NULL REFERENCES meta.load_manifest (load_id),
    source_file text NOT NULL,
    source_line integer NOT NULL,
    parse_error text,
    geolocation_zip_code_prefix text, geolocation_lat text, geolocation_lng text,
    geolocation_city text, geolocation_state text
);

CREATE TABLE IF NOT EXISTS raw.category_translation (
    load_id bigint NOT NULL REFERENCES meta.load_manifest (load_id),
    source_file text NOT NULL,
    source_line integer NOT NULL,
    parse_error text,
    product_category_name text, product_category_name_english text
);

CREATE INDEX IF NOT EXISTS raw_orders_load_idx ON raw.orders (load_id);
CREATE INDEX IF NOT EXISTS raw_order_items_load_idx ON raw.order_items (load_id);
CREATE INDEX IF NOT EXISTS raw_customers_load_idx ON raw.customers (load_id);
CREATE INDEX IF NOT EXISTS raw_sellers_load_idx ON raw.sellers (load_id);
CREATE INDEX IF NOT EXISTS raw_products_load_idx ON raw.products (load_id);
CREATE INDEX IF NOT EXISTS raw_payments_load_idx ON raw.payments (load_id);
CREATE INDEX IF NOT EXISTS raw_reviews_load_idx ON raw.reviews (load_id);
CREATE INDEX IF NOT EXISTS raw_geolocation_load_idx ON raw.geolocation (load_id);
CREATE INDEX IF NOT EXISTS raw_category_translation_load_idx ON raw.category_translation (load_id);
