-- LAB02 design draft. Only synthetic/deidentified data may be used before policy approval.
CREATE TABLE ticket_ref (
  ticket_id UUID PRIMARY KEY,
  external_ref TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE knowledge_article (
  article_id UUID PRIMARY KEY,
  source_key TEXT NOT NULL,
  version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft', 'approved', 'retired')),
  title TEXT NOT NULL,
  UNIQUE (source_key, version)
);
CREATE TABLE analysis_proposal (
  proposal_id UUID PRIMARY KEY,
  ticket_id UUID NOT NULL REFERENCES ticket_ref(ticket_id),
  mode TEXT NOT NULL CHECK (mode IN ('answer', 'clarify', 'escalate')),
  category TEXT NOT NULL CHECK (category IN ('access', 'payment', 'settings', 'integrations', 'product_error', 'other')),
  summary TEXT NOT NULL,
  draft_reply TEXT,
  model_version TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (mode = 'answer' OR draft_reply IS NULL)
);
CREATE TABLE proposal_evidence (
  proposal_id UUID NOT NULL REFERENCES analysis_proposal(proposal_id),
  article_id UUID NOT NULL REFERENCES knowledge_article(article_id),
  excerpt TEXT NOT NULL,
  PRIMARY KEY (proposal_id, article_id)
);
CREATE TABLE operator_review (
  review_id UUID PRIMARY KEY,
  proposal_id UUID NOT NULL REFERENCES analysis_proposal(proposal_id),
  actor_ref TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('accepted', 'edited', 'rejected')),
  final_text TEXT,
  reason TEXT,
  reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (decision <> 'rejected' OR final_text IS NULL)
);
CREATE INDEX analysis_proposal_ticket_id_idx ON analysis_proposal(ticket_id);
CREATE INDEX operator_review_proposal_id_idx ON operator_review(proposal_id);
