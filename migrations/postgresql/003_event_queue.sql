-- LAB02 event/job/attempt concepts adapted to the existing immutable LAB03 cards.
ALTER TABLE analysis_proposal ADD COLUMN input_version INTEGER;
CREATE TABLE ticket_snapshot (
 ticket_ref TEXT PRIMARY KEY,
 input_version INTEGER NOT NULL CHECK(input_version>0),
 input_json TEXT NOT NULL,
 job_id TEXT NOT NULL
);
CREATE TABLE helpdesk_snapshot (
 ticket_ref TEXT NOT NULL,
 input_version INTEGER NOT NULL CHECK(input_version>0),
 input_json TEXT NOT NULL,
 PRIMARY KEY(ticket_ref,input_version)
);
CREATE TABLE analysis_event (
 event_id TEXT PRIMARY KEY,
 event_hash TEXT NOT NULL,
 ticket_ref TEXT NOT NULL,
 input_version INTEGER NOT NULL CHECK(input_version>0),
 event_type TEXT NOT NULL CHECK(event_type IN ('ticket.created','customer.message_received')),
 kb_version TEXT NOT NULL,
 occurred_at TEXT NOT NULL,
 job_id TEXT NOT NULL UNIQUE,
 UNIQUE(event_id,ticket_ref,input_version),
 UNIQUE(ticket_ref,input_version)
);
CREATE TABLE analysis_job (
 job_id TEXT PRIMARY KEY,
 event_id TEXT NOT NULL,
 ticket_ref TEXT NOT NULL,
 input_version INTEGER NOT NULL,
 input_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('queued','running','ready','superseded','manual_review')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 2),
 created_at DOUBLE PRECISION NOT NULL,
 started_at DOUBLE PRECISION,
 deadline_at DOUBLE PRECISION NOT NULL,
 lease_until DOUBLE PRECISION,
 claim_id TEXT,
 not_before DOUBLE PRECISION NOT NULL DEFAULT 0,
 reason TEXT,
 proposal_id TEXT REFERENCES analysis_proposal(proposal_id),
 UNIQUE(ticket_ref,input_version),
 FOREIGN KEY(event_id,ticket_ref,input_version) REFERENCES analysis_event(event_id,ticket_ref,input_version)
);
CREATE UNIQUE INDEX one_running_job ON analysis_job(ticket_ref) WHERE state='running';
CREATE UNIQUE INDEX one_waiting_job ON analysis_job(ticket_ref) WHERE state='queued';
CREATE INDEX next_waiting_job ON analysis_job(created_at) WHERE state='queued';
CREATE TABLE model_attempt (
 job_id TEXT NOT NULL REFERENCES analysis_job(job_id),
 attempt_no INTEGER NOT NULL CHECK(attempt_no BETWEEN 1 AND 2),
 request_id TEXT NOT NULL UNIQUE,
 reserved_tokens INTEGER NOT NULL CHECK(reserved_tokens>0),
 state TEXT NOT NULL CHECK(state IN ('reserved','completed','unknown')),
 reserved_at DOUBLE PRECISION NOT NULL,
 PRIMARY KEY(job_id,attempt_no)
);
CREATE TABLE event_trace (
 trace_id TEXT PRIMARY KEY,
 job_id TEXT NOT NULL REFERENCES analysis_job(job_id),
 metadata_json TEXT NOT NULL
);
CREATE FUNCTION guard_event_review() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE latest INTEGER; proposed INTEGER;
BEGIN
 SELECT t.input_version,p.input_version INTO latest,proposed
 FROM analysis_proposal p JOIN ticket_snapshot t ON t.ticket_ref=p.ticket_ref
 WHERE p.proposal_id=NEW.proposal_id FOR UPDATE OF t;
 IF latest IS NOT NULL AND proposed IS DISTINCT FROM latest THEN
  RAISE EXCEPTION 'stale proposal' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER current_review BEFORE INSERT ON operator_review FOR EACH ROW EXECUTE FUNCTION guard_event_review();
CREATE FUNCTION immutable_event_proposal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'proposal is immutable' USING ERRCODE='23514'; END; $$;
CREATE TRIGGER immutable_proposal BEFORE UPDATE ON analysis_proposal FOR EACH ROW EXECUTE FUNCTION immutable_event_proposal();
