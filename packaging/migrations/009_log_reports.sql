-- 009: scheduled log digests / reports (feature F8)
CREATE TABLE IF NOT EXISTS log_reports (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  name           VARCHAR(255) NOT NULL,
  enabled        TINYINT(1)   NOT NULL DEFAULT 1,
  query          TEXT         NOT NULL,
  window_sec     INT          NOT NULL DEFAULT 86400,
  schedule_kind  VARCHAR(16)  NOT NULL DEFAULT 'daily',
  at_hour        INT          NOT NULL DEFAULT 7,
  at_minute      INT          NOT NULL DEFAULT 0,
  dow            INT          NULL,
  interval_hours INT          NULL,
  top_field      VARCHAR(64)  NOT NULL DEFAULT 'host',
  error_query    TEXT         NULL,
  severity       VARCHAR(32)  NOT NULL DEFAULT 'info',
  recipients     VARCHAR(255) NULL,
  last_run       DATETIME     NULL,
  next_run       DATETIME     NULL,
  last_output    TEXT         NULL,
  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_log_reports_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
