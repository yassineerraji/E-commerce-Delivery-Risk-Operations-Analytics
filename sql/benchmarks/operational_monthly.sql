-- Q2 — operational aggregation (dashboard overview): monthly KPIs of orders CONTAINING a
-- category, for one region. Parameters: %(categories)s text[], %(regions)s text[], %(start)s, %(end)s.
-- Input grain : order_summary (one row per order), order_categories (order x category)
-- Output grain: one row per purchase month
SELECT
    o.purchase_month,
    count(*)                                    AS orders,
    count(*) FILTER (WHERE o.label_eligible)    AS eligible_delivered,
    count(*) FILTER (WHERE o.is_late)           AS late,
    count(*) FILTER (WHERE o.is_late)::float / NULLIF(count(*) FILTER (WHERE o.label_eligible), 0) AS late_rate,
    sum(o.item_value_brl)                       AS item_value_brl
FROM order_summary AS o
WHERE o.purchase_at >= %(start)s AND o.purchase_at < %(end)s
  AND o.customer_region = ANY(%(regions)s::text[])
  AND EXISTS (SELECT 1 FROM order_categories AS c
              WHERE c.order_id = o.order_id AND c.category = ANY(%(categories)s::text[]))
GROUP BY o.purchase_month
ORDER BY o.purchase_month;
