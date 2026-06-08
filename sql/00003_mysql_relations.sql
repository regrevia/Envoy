CREATE TABLE IF NOT EXISTS semantic_units (
  id CHAR(36) PRIMARY KEY,
  project_id CHAR(36) NOT NULL,
  asset_id CHAR(36) NOT NULL,
  segment_id CHAR(36) NULL,
  unit_type VARCHAR(50) NOT NULL,
  title VARCHAR(255) NULL,
  content TEXT NOT NULL,
  labels JSON NULL,
  confidence DECIMAL(5,4) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_semantic_units_project_id (project_id),
  INDEX idx_semantic_units_asset_id (asset_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS generated_samples (
  id CHAR(36) PRIMARY KEY,
  project_id CHAR(36) NOT NULL,
  source_unit_id CHAR(36) NOT NULL,
  sample_type VARCHAR(32) NOT NULL,
  input_text TEXT NOT NULL,
  output_text TEXT NOT NULL,
  quality_score DECIMAL(5,2) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_generated_samples_project_id (project_id),
  INDEX idx_generated_samples_source_unit_id (source_unit_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS trace_links (
  id CHAR(36) PRIMARY KEY,
  source_type VARCHAR(50) NOT NULL,
  source_id CHAR(36) NOT NULL,
  target_type VARCHAR(50) NOT NULL,
  target_id CHAR(36) NOT NULL,
  relation_type VARCHAR(50) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_trace_links_source (source_type, source_id),
  INDEX idx_trace_links_target (target_type, target_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS review_records (
  id CHAR(36) PRIMARY KEY,
  project_id CHAR(36) NOT NULL,
  target_type VARCHAR(50) NOT NULL,
  target_id CHAR(36) NOT NULL,
  before_content TEXT NULL,
  after_content TEXT NULL,
  reviewer VARCHAR(100) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_review_records_project_id (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
