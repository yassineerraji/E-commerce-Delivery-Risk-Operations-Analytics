-- core.customers / core.sellers / core.products — one row per key, keys verified in staging.

CREATE TABLE core.customers AS
SELECT
    c.customer_id,
    c.customer_unique_id,
    c.zip_prefix_raw,
    c.zip_prefix,
    c.city,
    c.state,
    s.region
FROM staging.customers AS c
LEFT JOIN core.brazil_states AS s USING (state);
ALTER TABLE core.customers ADD PRIMARY KEY (customer_id);
CREATE INDEX ON core.customers (customer_unique_id);

CREATE TABLE core.sellers AS
SELECT
    sl.seller_id,
    sl.zip_prefix_raw,
    sl.zip_prefix,
    sl.city,
    sl.state,
    s.region
FROM staging.sellers AS sl
LEFT JOIN core.brazil_states AS s USING (state);
ALTER TABLE core.sellers ADD PRIMARY KEY (seller_id);

-- Category label: English translation, else the Portuguese name (untranslated), else 'missing'.
CREATE TABLE core.products AS
SELECT
    p.product_id,
    p.category_name_pt,
    t.category_name_en,
    coalesce(t.category_name_en, p.category_name_pt, 'missing_category') AS category,
    (p.category_name_pt IS NOT NULL AND t.category_name_en IS NULL)      AS category_untranslated,
    p.name_length,
    p.description_length,
    p.photos_qty,
    p.weight_g,
    p.length_cm,
    p.height_cm,
    p.width_cm,
    p.length_cm * p.height_cm * p.width_cm AS volume_cm3
FROM staging.products AS p
LEFT JOIN staging.category_translation AS t USING (category_name_pt);
ALTER TABLE core.products ADD PRIMARY KEY (product_id);
