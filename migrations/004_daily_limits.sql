-- Additive migration. Existing audit/card rows and prior reservations are retained.
ALTER TABLE analysis_job ADD COLUMN actor_ref TEXT NOT NULL DEFAULT 'demo-operator';
ALTER TABLE analysis_job ADD COLUMN inference_mode TEXT;
CREATE TABLE daily_usage (
 actor_ref TEXT NOT NULL,
 usage_day TEXT NOT NULL,
 requests INTEGER NOT NULL DEFAULT 0 CHECK(requests>=0),
 reserved_tokens BIGINT NOT NULL DEFAULT 0 CHECK(reserved_tokens>=0),
 PRIMARY KEY(actor_ref,usage_day)
);
CREATE TABLE security_event (
 event_id TEXT PRIMARY KEY,
 event_type TEXT NOT NULL,
 reason TEXT NOT NULL,
 created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE inference_call (
 claim_id TEXT PRIMARY KEY,
 started_at DOUBLE PRECISION NOT NULL
);
