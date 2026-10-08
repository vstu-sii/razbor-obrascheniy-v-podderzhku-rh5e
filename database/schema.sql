-- Migration 001: triage-store owns this database. Synthetic/deidentified data only.
BEGIN;
CREATE TABLE schema_migration (version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE ticket_ref (
  ticket_id UUID PRIMARY KEY,
  external_ref TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE analysis_proposal (
  proposal_id UUID PRIMARY KEY,
  ticket_id UUID NOT NULL REFERENCES ticket_ref(ticket_id),
  request_id TEXT NOT NULL UNIQUE,
  kb_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('proposed', 'not_analyzed')),
  mode TEXT NOT NULL CHECK (mode IN ('answer', 'clarify', 'escalate')),
  category TEXT NOT NULL CHECK (category IN ('access', 'payment', 'settings', 'integrations', 'product_error', 'other')),
  urgency TEXT NOT NULL CHECK (urgency IN ('unassessed', 'low', 'medium', 'high', 'critical')),
  urgency_reason TEXT NOT NULL,
  summary TEXT NOT NULL,
  known_facts JSONB NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(known_facts) = 'array'),
  missing_data JSONB NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(missing_data) = 'array'),
  next_step TEXT NOT NULL,
  draft_reply TEXT,
  model_version TEXT,
  needs_human_review BOOLEAN NOT NULL DEFAULT true CHECK (needs_human_review),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (status <> 'not_analyzed' OR (mode = 'escalate' AND draft_reply IS NULL))
);
-- Immutable source/version snapshot, not a foreign key into another service's DB.
CREATE TABLE proposal_evidence (
  proposal_id UUID NOT NULL REFERENCES analysis_proposal(proposal_id),
  source_key TEXT NOT NULL,
  source_version TEXT NOT NULL,
  title TEXT NOT NULL,
  excerpt TEXT NOT NULL,
  PRIMARY KEY (proposal_id, source_key, source_version)
);
CREATE TABLE operator_review (
  review_id UUID PRIMARY KEY,
  proposal_id UUID NOT NULL REFERENCES analysis_proposal(proposal_id),
  actor_ref TEXT NOT NULL CHECK (length(trim(actor_ref)) > 0),
  decision TEXT NOT NULL CHECK (decision IN ('accepted', 'edited', 'rejected')),
  action TEXT NOT NULL CHECK (action IN ('none', 'send_answer', 'send_clarification', 'escalate')),
  final_text TEXT,
  destination TEXT,
  reason TEXT,
  confirmed_at TIMESTAMPTZ,
  reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (decision <> 'rejected' OR (action = 'none' AND final_text IS NULL AND confirmed_at IS NULL AND reason IS NOT NULL AND length(trim(reason)) > 0)),
  CHECK (action = 'none' OR (decision <> 'rejected' AND confirmed_at IS NOT NULL)),
  CHECK (action NOT IN ('send_answer', 'send_clarification') OR (final_text IS NOT NULL AND length(trim(final_text)) > 0)),
  CHECK (action <> 'escalate' OR (destination IS NOT NULL AND length(trim(destination)) > 0))
);
CREATE INDEX analysis_proposal_ticket_id_idx ON analysis_proposal(ticket_id);
CREATE INDEX operator_review_proposal_id_idx ON operator_review(proposal_id);
INSERT INTO schema_migration(version) VALUES (1);
COMMIT;
