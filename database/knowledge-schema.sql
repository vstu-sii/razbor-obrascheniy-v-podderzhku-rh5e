-- Migration 001: knowledge service owns this separate database.
BEGIN;
CREATE TABLE schema_migration (version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE knowledge_article (
  article_id UUID PRIMARY KEY,
  source_key TEXT NOT NULL,
  version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft', 'approved', 'retired')),
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  approved_at TIMESTAMPTZ,
  UNIQUE (source_key, version),
  CHECK (status <> 'approved' OR approved_at IS NOT NULL)
);
INSERT INTO schema_migration(version) VALUES (1);
COMMIT;
