-- Vexor migration 002 — schema drift fixes discovered in pre-prod review.
-- Idempotent: uses ADD COLUMN IF NOT EXISTS (MariaDB 10.0.2+).
-- Applied automatically by vexor-logs RPM postinstall.

ALTER TABLE hosts            ADD COLUMN IF NOT EXISTS last_state    TINYINT      DEFAULT -1;
ALTER TABLE report_schedules ADD COLUMN IF NOT EXISTS name          VARCHAR(64)  NOT NULL DEFAULT "unnamed";
ALTER TABLE report_schedules ADD COLUMN IF NOT EXISTS params        LONGTEXT;
ALTER TABLE report_schedules ADD COLUMN IF NOT EXISTS enabled       TINYINT(1)   DEFAULT 1;
ALTER TABLE report_schedules ADD COLUMN IF NOT EXISTS last_status   VARCHAR(255) DEFAULT "";
