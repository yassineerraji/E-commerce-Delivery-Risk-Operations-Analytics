-- core.order_items, core.payments, core.reviews — child tables of core.orders.
-- Each keeps its own grain; they are NEVER joined to each other on order_id before aggregation.

-- ---------------------------------------------------------------- order_items
CREATE TEMP TABLE items_ref ON COMMIT DROP AS
SELECT
    i.*,
    CASE
        WHEN o.order_id IS NULL AND EXISTS (SELECT 1 FROM quarantined_keys q WHERE q.dataset = 'orders' AND q.key_value = i.order_id)
            THEN 'parent_order_quarantined'
        WHEN o.order_id IS NULL THEN 'orphan_order_reference'
        WHEN p.product_id IS NULL AND EXISTS (SELECT 1 FROM quarantined_keys q WHERE q.dataset = 'products' AND q.key_value = i.product_id)
            THEN 'parent_product_quarantined'
        WHEN p.product_id IS NULL THEN 'orphan_product_reference'
        WHEN s.seller_id IS NULL AND EXISTS (SELECT 1 FROM quarantined_keys q WHERE q.dataset = 'sellers' AND q.key_value = i.seller_id)
            THEN 'parent_seller_quarantined'
        WHEN s.seller_id IS NULL THEN 'orphan_seller_reference'
    END AS ref_issue
FROM staging.order_items AS i
LEFT JOIN core.orders   AS o USING (order_id)
LEFT JOIN core.products AS p USING (product_id)
LEFT JOIN core.sellers  AS s USING (seller_id);

INSERT INTO quality.quarantine (dataset, load_id, source_line, reason, raw_record)
SELECT 'order_items', load_id, source_line, ref_issue, to_jsonb(r) - 'ref_issue'
FROM items_ref AS r WHERE ref_issue IS NOT NULL;

CREATE TABLE core.order_items AS
SELECT order_id, order_item_id, product_id, seller_id, shipping_limit_at, price_brl, freight_brl
FROM items_ref WHERE ref_issue IS NULL;
ALTER TABLE core.order_items ADD PRIMARY KEY (order_id, order_item_id);
ALTER TABLE core.order_items ADD FOREIGN KEY (order_id)   REFERENCES core.orders (order_id);
ALTER TABLE core.order_items ADD FOREIGN KEY (product_id) REFERENCES core.products (product_id);
ALTER TABLE core.order_items ADD FOREIGN KEY (seller_id)  REFERENCES core.sellers (seller_id);
CREATE INDEX ON core.order_items (seller_id);
CREATE INDEX ON core.order_items (product_id);

-- ---------------------------------------------------------------- payments
CREATE TEMP TABLE payments_ref ON COMMIT DROP AS
SELECT
    p.*,
    CASE
        WHEN o.order_id IS NOT NULL THEN NULL
        WHEN EXISTS (SELECT 1 FROM quarantined_keys q WHERE q.dataset = 'orders' AND q.key_value = p.order_id)
            THEN 'parent_order_quarantined'
        ELSE 'orphan_order_reference'
    END AS ref_issue
FROM staging.payments AS p
LEFT JOIN core.orders AS o USING (order_id);

INSERT INTO quality.quarantine (dataset, load_id, source_line, reason, raw_record)
SELECT 'payments', load_id, source_line, ref_issue, to_jsonb(r) - 'ref_issue'
FROM payments_ref AS r WHERE ref_issue IS NOT NULL;

CREATE TABLE core.payments AS
SELECT order_id, payment_sequential, payment_type, installments, payment_value_brl
FROM payments_ref WHERE ref_issue IS NULL;
ALTER TABLE core.payments ADD PRIMARY KEY (order_id, payment_sequential);
ALTER TABLE core.payments ADD FOREIGN KEY (order_id) REFERENCES core.orders (order_id);

-- ---------------------------------------------------------------- reviews
CREATE TEMP TABLE reviews_ref ON COMMIT DROP AS
SELECT
    r.*,
    CASE
        WHEN o.order_id IS NOT NULL THEN NULL
        WHEN EXISTS (SELECT 1 FROM quarantined_keys q WHERE q.dataset = 'orders' AND q.key_value = r.order_id)
            THEN 'parent_order_quarantined'
        ELSE 'orphan_order_reference'
    END AS ref_issue
FROM staging.reviews AS r
LEFT JOIN core.orders AS o USING (order_id);

INSERT INTO quality.quarantine (dataset, load_id, source_line, reason, raw_record)
SELECT 'reviews', load_id, source_line, ref_issue, to_jsonb(x) - 'ref_issue'
FROM reviews_ref AS x WHERE ref_issue IS NOT NULL;

CREATE TABLE core.reviews AS
SELECT review_id, order_id, review_score, has_comment_title, has_comment_message,
       comment_message_length, review_created_at, review_answered_at
FROM reviews_ref WHERE ref_issue IS NULL;
ALTER TABLE core.reviews ADD PRIMARY KEY (review_id, order_id);
ALTER TABLE core.reviews ADD FOREIGN KEY (order_id) REFERENCES core.orders (order_id);
CREATE INDEX ON core.reviews (order_id);
