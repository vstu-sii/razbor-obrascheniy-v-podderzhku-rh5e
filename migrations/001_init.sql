PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES (1);
CREATE TABLE IF NOT EXISTS analysis_proposal (
  proposal_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL UNIQUE,
  input_hash TEXT NOT NULL,
  ticket_ref TEXT NOT NULL,
  card_json TEXT NOT NULL,
  diagnostics_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS operator_review (
  review_id TEXT PRIMARY KEY,
  proposal_id TEXT NOT NULL REFERENCES analysis_proposal(proposal_id),
  actor_ref TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('accepted','edited','rejected')),
  final_text TEXT,
  reason TEXT NOT NULL,
  reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (decision <> 'rejected' OR final_text IS NULL)
);
CREATE INDEX IF NOT EXISTS review_proposal_idx ON operator_review(proposal_id);
