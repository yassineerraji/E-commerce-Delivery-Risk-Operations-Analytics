-- 001_meta.sql — schemas, load manifest, publication log and parsing helpers.

CREATE SCHEMA IF NOT EXISTS meta;
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS feature;
CREATE SCHEMA IF NOT EXISTS evaluation;
CREATE SCHEMA IF NOT EXISTS quality;

-- One row per imported file. (dataset, sha256) is unique: re-importing the same file is a no-op.
CREATE TABLE IF NOT EXISTS meta.load_manifest (
    load_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id         text        NOT NULL,
    dataset          text        NOT NULL,
    file_name        text        NOT NULL,
    file_size_bytes  bigint      NOT NULL,
    sha256           text        NOT NULL,
    row_count        bigint,
    parse_errors     bigint,
    source_name      text        NOT NULL,
    source_version   text,
    data_mode        text        NOT NULL CHECK (data_mode IN ('real', 'demo', 'test', 'e2e')),
    acquired_at      timestamptz,
    acquired_at_basis text       NOT NULL,
    loaded_at        timestamptz NOT NULL DEFAULT now(),
    status           text        NOT NULL CHECK (status IN ('loading', 'loaded')),
    UNIQUE (dataset, sha256)
);

-- Every publication of a layer (transform, features, predictions) is logged here.
CREATE TABLE IF NOT EXISTS meta.publication_log (
    publication_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    layer          text        NOT NULL,
    published_at   timestamptz NOT NULL DEFAULT now(),
    data_mode      text        NOT NULL,
    details        jsonb       NOT NULL DEFAULT '{}'::jsonb
);

-- The most recent successfully loaded file of a dataset is the one transformed.
CREATE OR REPLACE FUNCTION meta.current_load_id(p_dataset text)
RETURNS bigint LANGUAGE sql STABLE AS $$
    SELECT max(load_id) FROM meta.load_manifest WHERE dataset = p_dataset AND status = 'loaded'
$$;

-- Strict ISO format check (avoids DateStyle-dependent interpretation of ambiguous dates).
CREATE OR REPLACE FUNCTION meta.is_iso_timestamp(p text)
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT p ~ '^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$'
           AND pg_input_is_valid(p, 'timestamp')
$$;

CREATE OR REPLACE FUNCTION meta.is_decimal(p text)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT p ~ '^-?\d+(\.\d+)?$'
$$;

-- Integer written either as "3" or "3.0" (some exports write integers as floats).
CREATE OR REPLACE FUNCTION meta.is_integer_like(p text)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT p ~ '^-?\d+(\.0+)?$'
$$;

-- Brazilian CEP prefixes have 5 digits; CSV round-trips through numeric tools drop leading zeros.
-- Returns NULL when the raw value is not 1 to 5 digits (the raw value is always kept as well).
CREATE OR REPLACE FUNCTION meta.zip_prefix(p text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN btrim(p) ~ '^\d{1,5}$' THEN lpad(btrim(p), 5, '0') END
$$;

-- Great-circle distance in km between two (lat, lng) points; NULL if any input is NULL.
CREATE OR REPLACE FUNCTION meta.haversine_km(lat1 float8, lng1 float8, lat2 float8, lng2 float8)
RETURNS float8 LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT 2 * 6371.0 * asin(sqrt(
        power(sin(radians(lat2 - lat1) / 2), 2)
        + cos(radians(lat1)) * cos(radians(lat2)) * power(sin(radians(lng2 - lng1) / 2), 2)
    ))
$$;
