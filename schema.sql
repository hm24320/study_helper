CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  verify_method TEXT NOT NULL,
  due_at TIMESTAMP NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('PENDING', 'APPROVED', 'EXPIRED')),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tasks_state_due ON tasks (state, due_at);

CREATE TABLE IF NOT EXISTS verification_attempts (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  proof_url TEXT NOT NULL,
  verdict INTEGER NOT NULL CHECK (verdict IN (0, 1)),
  score REAL,
  reasons TEXT,
  raw_features TEXT,
  FOREIGN KEY (task_id) REFERENCES tasks (id)
);
