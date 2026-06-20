-- Migration 005: log-alert history (state-change events) + last_state column.
-- Idempotent: safe to re-run.

-- Track the last submitted Naemon return code so the evaluator can detect
-- OK<->WARN<->CRIT transitions.
ALTER TABLE log_alert_rules
  ADD COLUMN IF NOT EXISTS last_state INT NULL;

-- Append-only history of log-alert state changes.
CREATE TABLE IF NOT EXISTS log_alert_events (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  rule_id     INT NULL,
  rule_name   VARCHAR(255) NOT NULL,
  host        VARCHAR(255) NULL,
  mode        VARCHAR(16) NOT NULL DEFAULT 'match',
  severity    VARCHAR(32) NULL,
  state       INT NOT NULL,
  prev_state  INT NULL,
  count       INT NOT NULL DEFAULT 0,
  output      TEXT NULL,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_lae_host_id (host, id),
  INDEX idx_lae_rule_id (rule_id, id),
  INDEX idx_lae_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
