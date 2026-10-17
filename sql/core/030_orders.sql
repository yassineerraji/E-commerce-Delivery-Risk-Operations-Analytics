-- core.orders — one row per order. Timestamps are kept WITHOUT time zone: the source does not
-- document one (presumably Brazilian local time); no UTC conversion is invented.

CREATE TEMP TABLE orders_ref ON COMMIT DROP AS
SELECT
    o.*,
    CASE
        WHEN c.customer_id IS NOT NULL THEN NULL
        WHEN EXISTS (SELECT 1 FROM quarantined_keys q WHERE q.dataset = 'customers' AND q.key_value = o.customer_id)
            THEN 'parent_customer_quarantined'
        ELSE 'orphan_customer_reference'
    END AS ref_issue
FROM staging.orders AS o
LEFT JOIN core.customers AS c USING (customer_id);

INSERT INTO quality.quarantine (dataset, load_id, source_line, reason, raw_record)
SELECT 'orders', load_id, source_line, ref_issue, to_jsonb(r) - 'ref_issue'
FROM orders_ref AS r WHERE ref_issue IS NOT NULL;

-- Orders removed here also cascade to their children.
INSERT INTO quarantined_keys (dataset, key_value)
SELECT 'orders', order_id FROM orders_ref WHERE ref_issue IS NOT NULL;

CREATE TABLE core.orders AS
SELECT
    order_id,
    customer_id,
    order_status,
    purchase_at,
    approved_at,
    delivered_carrier_at,
    delivered_customer_at,
    estimated_delivery_at
FROM orders_ref
WHERE ref_issue IS NULL;
ALTER TABLE core.orders ADD PRIMARY KEY (order_id);
ALTER TABLE core.orders ADD FOREIGN KEY (customer_id) REFERENCES core.customers (customer_id);
CREATE INDEX ON core.orders (approved_at);
CREATE INDEX ON core.orders (purchase_at);
