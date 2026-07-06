-- 008: log-derived metrics + samples (feature F3)
CREATE TABLE IF NOT EXISTS log_metrics (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  name           VARCHAR(255) NOT NULL,
  query          TEXT         NOT NULL,
  agg            VARCHAR(16)  NOT NULL DEFAULT 'count',
  window_sec     INT          NOT NULL DEFAULT 300,
  group_by       VARCHAR(64)  NULL,
  unit           VARCHAR(32)  NULL,
  enabled        TINYINT(1)   NOT NULL DEFAULT 1,
  warn_threshold DOUBLE       NULL,
  crit_threshold DOUBLE       NULL,
  severity       VARCHAR(32)  NOT NULL DEFAULT 'warning',
  host_binding   VARCHAR(255) NULL,
  last_value     DOUBLE       NULL,
  last_state     INT          NULL,
  last_run       DATETIME     NULL,
  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_log_metrics_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS log_metric_samples (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  metric_id  INT          NOT NULL,
  ts         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  host       VARCHAR(255) NULL,
  value      DOUBLE       NOT NULL DEFAULT 0,
  KEY idx_log_metric_samples_metric_ts (metric_id, ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
