-- 000_reset.sql — quality tables describe the transform being built (same transaction).
-- If the transform fails, the TRUNCATE is rolled back with everything else.
TRUNCATE quality.quarantine, quality.duplicate_log;
