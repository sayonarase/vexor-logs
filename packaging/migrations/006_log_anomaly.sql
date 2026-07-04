-- 006_log_anomaly.sql : anomaly-detection monitors, events and templates.
-- Idempotent (CREATE TABLE IF NOT EXISTS). Applied by vexor-logs postinstall.

CREATE TABLE IF NOT EXISTS log_anomaly_monitors (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  name             VARCHAR(255) NOT NULL UNIQUE,
  kind             VARCHAR(16)  NOT NULL DEFAULT 'baseline',
  query            TEXT         NOT NULL,
  enabled          TINYINT(1)   NOT NULL DEFAULT 1,
  window_sec       INT          NOT NULL DEFAULT 300,
  baseline_sec     INT          NOT NULL DEFAULT 86400,
  sensitivity      FLOAT        NOT NULL DEFAULT 3.0,
  direction        VARCHAR(8)   NOT NULL DEFAULT 'both',
  min_baseline     FLOAT        NOT NULL DEFAULT 0,
  min_interval_sec INT          NOT NULL DEFAULT 0,
  severity         VARCHAR(32)  NOT NULL DEFAULT 'warning',
  host_binding     VARCHAR(255) NULL,
  nl_question      TEXT         NULL,
  preset_id        VARCHAR(64)  NULL,
  last_state       INT          NULL,
  last_score       FLOAT        NULL,
  last_run         DATETIME     NULL,
  created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS log_anomaly_events (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  monitor_id    INT          NULL,
  monitor_name  VARCHAR(255) NOT NULL,
  kind          VARCHAR(16)  NOT NULL DEFAULT 'baseline',
  host          VARCHAR(255) NULL,
  severity      VARCHAR(32)  NULL,
  state         INT          NOT NULL,
  prev_state    INT          NULL,
  score         FLOAT        NULL,
  observed      FLOAT        NULL,
  baseline_mean FLOAT        NULL,
  baseline_std  FLOAT        NULL,
  template      TEXT         NULL,
  sample        TEXT         NULL,
  output        TEXT         NULL,
  llm_note      TEXT         NULL,
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_anom_ev_monitor (monitor_id),
  INDEX idx_anom_ev_host (host),
  INDEX idx_anom_ev_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS log_anomaly_templates (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  monitor_id INT          NOT NULL,
  signature  VARCHAR(40)  NOT NULL,
  template   TEXT         NULL,
  hits       INT          NOT NULL DEFAULT 1,
  first_seen DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_anom_tmpl (monitor_id, signature),
  INDEX idx_anom_tmpl_monitor (monitor_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
