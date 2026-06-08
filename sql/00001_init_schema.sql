CREATE TABLE IF NOT EXISTS projects (
  id CHAR(36) PRIMARY KEY,
  user_id CHAR(36) NULL,
  name VARCHAR(255) NOT NULL,
  description TEXT NULL,
  domain_type VARCHAR(100) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'active',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS assets (
  id CHAR(36) PRIMARY KEY,
  project_id CHAR(36) NOT NULL,
  asset_type VARCHAR(32) NOT NULL,
  file_name VARCHAR(255) NOT NULL,
  file_path TEXT NOT NULL,
  mime_type VARCHAR(255) NULL,
  duration_sec INT NULL,
  language VARCHAR(32) NULL,
  metadata JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_assets_project_id (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS processing_tasks (
  id CHAR(36) PRIMARY KEY,
  project_id CHAR(36) NOT NULL,
  asset_id CHAR(36) NULL,
  task_type VARCHAR(50) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  progress INT NOT NULL DEFAULT 0,
  input_config JSON NULL,
  result_summary TEXT NULL,
  error_message TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_tasks_project_id (project_id),
  INDEX idx_tasks_asset_id (asset_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS artifacts (
  id CHAR(36) PRIMARY KEY,
  project_id CHAR(36) NOT NULL,
  artifact_type VARCHAR(64) NOT NULL,
  name VARCHAR(255) NOT NULL,
  file_path TEXT NOT NULL,
  version VARCHAR(50) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_artifacts_project_id (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
