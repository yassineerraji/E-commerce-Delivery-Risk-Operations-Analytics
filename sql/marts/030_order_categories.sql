-- mart.order_categories — one row per (order, product category).
-- Used by the dashboard category filter: an order is selected if it CONTAINS at least one
-- item of a selected category (EXISTS), so it is counted once even if it matches several
-- categories. Order-level amounts then cover ALL items of the selected orders; the amounts
-- restricted to the selected categories are available here (item_value_brl per category).

DROP TABLE IF EXISTS mart.order_categories;
CREATE TABLE mart.order_categories AS
SELECT
    i.order_id,
    p.category,
    count(*)           AS n_items,
    sum(i.price_brl)   AS item_value_brl,
    sum(i.freight_brl) AS freight_value_brl
FROM core.order_items AS i
JOIN core.products AS p USING (product_id)
GROUP BY i.order_id, p.category;

ALTER TABLE mart.order_categories ADD PRIMARY KEY (order_id, category);
CREATE INDEX ON mart.order_categories (category);
