-- Vexor migration 003 — log-data-driven checks (mode=match|absence + thresholds).
-- Idempotent: ADD COLUMN IF NOT EXISTS (MariaDB 10.0.2+).
-- Applied automatically by vexor-logs RPM postinstall.

ALTER TABLE log_alert_rules ADD COLUMN IF NOT EXISTS mode           VARCHAR(16)  NOT NULL DEFAULT 'match';
ALTER TABLE log_alert_rules ADD COLUMN IF NOT EXISTS warn_threshold INT          NULL;
ALTER TABLE log_alert_rules ADD COLUMN IF NOT EXISTS crit_threshold INT          NULL;
ALTER TABLE log_alert_rules ADD COLUMN IF NOT EXISTS level_filter   VARCHAR(32)  NULL;
ALTER TABLE log_alert_rules ADD COLUMN IF NOT EXISTS group_by_host  TINYINT(1)   NOT NULL DEFAULT 0;
ALTER TABLE log_alert_rules ADD COLUMN IF NOT EXISTS preset_id      VARCHAR(64)  NULL;
