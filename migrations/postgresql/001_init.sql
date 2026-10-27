-- PostgreSQL equivalent of the existing LAB03 SQLite schema. Applied once, transactionally.
CREATE TABLE analysis_proposal (
 proposal_id TEXT PRIMARY KEY,
 request_id TEXT NOT NULL UNIQUE,
 input_hash TEXT NOT NULL,
 ticket_ref TEXT NOT NULL,
 card_json TEXT NOT NULL CHECK (jsonb_typeof(card_json::jsonb) = 'object'),
 diagnostics_json TEXT NOT NULL CHECK (jsonb_typeof(diagnostics_json::jsonb) = 'object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE operator_review (
 review_id TEXT PRIMARY KEY,
 proposal_id TEXT NOT NULL REFERENCES analysis_proposal(proposal_id),
 actor_ref TEXT NOT NULL,
 decision TEXT NOT NULL CHECK (decision IN ('accepted','edited','rejected')),
 final_text TEXT,
 reason TEXT NOT NULL,
 reviewed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK (decision <> 'rejected' OR final_text IS NULL)
);
CREATE INDEX review_proposal_idx ON operator_review(proposal_id);
