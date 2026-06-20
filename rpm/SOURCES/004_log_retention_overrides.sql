-- Vexor migration 004 — per-host log retention overrides.
-- Idempotent. Applied automatically by vexor-logs RPM postinstall.

CREATE TABLE IF NOT EXISTS log_retention_overrides (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    host           VARCHAR(255) NOT NULL,
    retention_days INT          NOT NULL,
    note           VARCHAR(255) NULL,
    created_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_log_retention_host (host)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
