-- 000_prepare.sql — drop core tables (dependency order) and list quarantined parent keys.
--
-- Child rows whose parent is missing are split in two cases:
--   parent_<x>_quarantined : the parent exists in the source but failed staging -> cascade,
--                            traced, non-critical;
--   orphan_<x>_reference   : the parent key does not exist in the source at all -> critical,
--                            blocks publication (see sql/assertions).

DROP TABLE IF EXISTS core.reviews, core.payments, core.order_sellers, core.order_items,
    core.orders, core.products, core.customers, core.sellers, core.geo_prefix, core.brazil_states CASCADE;

CREATE TEMP TABLE quarantined_keys ON COMMIT DROP AS
SELECT DISTINCT dataset, btrim(raw_record ->> key_name) AS key_value
FROM quality.quarantine
CROSS JOIN LATERAL (
    VALUES ('orders', 'order_id'), ('customers', 'customer_id'),
           ('products', 'product_id'), ('sellers', 'seller_id')
) AS k(ds, key_name)
WHERE quality.quarantine.dataset = k.ds;
