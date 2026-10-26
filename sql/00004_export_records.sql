CREATE TABLE IF NOT EXISTS export_records (
  id CHAR(36) PRIMARY KEY,
  project_id CHAR(36) NOT NULL,
  task_id CHAR(36) NOT NULL,
  export_type VARCHAR(50) NOT NULL,
  task_name VARCHAR(255) NULL,
  root_dir TEXT NOT NULL,
  file_list TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_export_records_project_id (project_id),
  INDEX idx_export_records_task_id (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
