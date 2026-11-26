CREATE TABLE provider_runtime_state (
  provider TEXT PRIMARY KEY,
  next_call_at DOUBLE PRECISION NOT NULL DEFAULT 0,
  cooldown_until DOUBLE PRECISION NOT NULL DEFAULT 0,
  next_reconcile_at DOUBLE PRECISION NOT NULL DEFAULT 0
);
INSERT INTO provider_runtime_state(provider) VALUES ('mistral'), ('sol');
CREATE TABLE provider_billing_receipt (
  provider TEXT NOT NULL,
  receipt_id TEXT NOT NULL,
  attempt_id TEXT NOT NULL UNIQUE REFERENCES model_attempt(request_id),
  billed_micro_usd BIGINT NOT NULL CHECK(billed_micro_usd >= 0),
  receipt_status TEXT NOT NULL,
  settled_at DOUBLE PRECISION NOT NULL,
  PRIMARY KEY(provider, receipt_id)
);
