-- LAB03 cascade ledger, additive to immutable proposals and queue.
ALTER TABLE analysis_job ADD COLUMN model_route TEXT NOT NULL DEFAULT 'single';
ALTER TABLE model_attempt ADD COLUMN provider TEXT;
ALTER TABLE model_attempt ADD COLUMN model TEXT;
ALTER TABLE model_attempt ADD COLUMN reserved_micro_usd BIGINT NOT NULL DEFAULT 0 CHECK(reserved_micro_usd>=0);
ALTER TABLE model_attempt ADD COLUMN billed_micro_usd BIGINT CHECK(billed_micro_usd>=0);
ALTER TABLE model_attempt ADD COLUMN diagnostics_json TEXT;
