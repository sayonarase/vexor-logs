-- 007: log field-extraction / parse rules (feature F2)
CREATE TABLE IF NOT EXISTS log_parse_rules (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  name         VARCHAR(255) NOT NULL,
  source_field VARCHAR(64)  NOT NULL DEFAULT '_msg',
  pattern      TEXT         NOT NULL,
  pattern_type VARCHAR(16)  NOT NULL DEFAULT 'pattern',
  enabled      TINYINT(1)   NOT NULL DEFAULT 1,
  sort_order   INT          NOT NULL DEFAULT 100,
  note         VARCHAR(255) NULL,
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_log_parse_rules_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
