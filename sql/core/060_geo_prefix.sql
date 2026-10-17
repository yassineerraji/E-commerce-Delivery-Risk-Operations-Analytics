-- core.geo_prefix — one row per 5-digit zip prefix, aggregated BEFORE any join to orders.
-- Input grain : staging.geolocation (several rows per prefix, many exact duplicates)
-- Method      : 1) collapse identical (prefix, lat, lng) points;
--               2) flag points outside the Brazil bounding box (config) as outliers;
--               3) take the marginal median of latitude and of longitude of non-outlier points.
-- The median is robust to remaining stray points. Prefix centroids only approximate the
-- position of a customer or seller (true addresses are not in the data).

CREATE TABLE core.geo_prefix AS
WITH points AS (
    SELECT
        zip_prefix,
        lat,
        lng,
        count(*) AS n_raw_rows,
        (lat BETWEEN current_setting('dr.lat_min')::float AND current_setting('dr.lat_max')::float
         AND lng BETWEEN current_setting('dr.lng_min')::float AND current_setting('dr.lng_max')::float) AS in_bbox
    FROM staging.geolocation
    GROUP BY zip_prefix, lat, lng
)
SELECT
    zip_prefix,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY lat) FILTER (WHERE in_bbox) AS lat,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY lng) FILTER (WHERE in_bbox) AS lng,
    sum(n_raw_rows)::integer                  AS n_raw_rows,
    count(*) FILTER (WHERE in_bbox)::integer  AS n_distinct_points,
    count(*) FILTER (WHERE NOT in_bbox)::integer AS n_outlier_points
FROM points
GROUP BY zip_prefix;
ALTER TABLE core.geo_prefix ADD PRIMARY KEY (zip_prefix);
