-- LAB02 additive queue design, apply once after schema.sql.
BEGIN;
ALTER TABLE ticket_ref ADD COLUMN current_input_version BIGINT NOT NULL DEFAULT 1 CHECK(current_input_version>0);
ALTER TABLE analysis_proposal ADD COLUMN input_version BIGINT NOT NULL DEFAULT 1 CHECK(input_version>0);
CREATE TABLE analysis_event (
 event_id TEXT PRIMARY KEY CHECK(length(event_id) BETWEEN 1 AND 128),
 ticket_id UUID NOT NULL REFERENCES ticket_ref(ticket_id),
 input_version BIGINT NOT NULL CHECK(input_version>0),
 event_type TEXT NOT NULL CHECK(event_type IN ('ticket.created','customer.message_received')),
 kb_version TEXT NOT NULL CHECK(length(kb_version) BETWEEN 1 AND 64),
 occurred_at TIMESTAMPTZ NOT NULL,
 received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(ticket_id,input_version),
 UNIQUE(event_id,ticket_id,input_version)
);
CREATE TABLE analysis_job (
 job_id UUID PRIMARY KEY,
 ticket_id UUID NOT NULL REFERENCES ticket_ref(ticket_id),
 input_version BIGINT NOT NULL CHECK(input_version>0),
 event_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('queued','running','ready','superseded','manual_review')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 2),
 reserved_tokens BIGINT NOT NULL DEFAULT 0 CHECK(reserved_tokens>=0),
 lease_until TIMESTAMPTZ,
 deadline_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(ticket_id,input_version),
 FOREIGN KEY(event_id,ticket_id,input_version) REFERENCES analysis_event(event_id,ticket_id,input_version)
);
CREATE UNIQUE INDEX one_running_job ON analysis_job(ticket_id) WHERE state='running';
CREATE UNIQUE INDEX one_waiting_job ON analysis_job(ticket_id) WHERE state='queued';
CREATE INDEX next_waiting_job ON analysis_job(created_at) WHERE state='queued';
-- Lock ticket row, supersede older queued job before inserting latest version.
-- Verify version under the same lock before publishing a result or confirming review.
-- Persistent per-attempt reservation: unknown billing must not reset after restart.
CREATE TABLE model_attempt (
 job_id UUID NOT NULL REFERENCES analysis_job(job_id),
 attempt_no INTEGER NOT NULL CHECK(attempt_no BETWEEN 1 AND 2),
 request_id UUID NOT NULL UNIQUE,
 reserved_tokens BIGINT NOT NULL CHECK(reserved_tokens > 0),
 state TEXT NOT NULL CHECK(state IN ('reserved','completed','unknown')),
 reserved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(job_id,attempt_no)
);
ALTER TABLE operator_review ADD COLUMN input_version BIGINT;
-- Older historical reviews stay NULL: do not invent an input version.
CREATE FUNCTION guard_current_review() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE current_version BIGINT; proposal_version BIGINT; target_ticket UUID;
BEGIN
 SELECT ticket_id,input_version INTO target_ticket,proposal_version FROM analysis_proposal WHERE proposal_id=NEW.proposal_id;
 SELECT current_input_version INTO current_version FROM ticket_ref WHERE ticket_id=target_ticket FOR UPDATE;
 IF proposal_version IS DISTINCT FROM current_version THEN
   RAISE EXCEPTION 'stale proposal' USING ERRCODE='23514';
 END IF;
 NEW.input_version := proposal_version;
 RETURN NEW;
END $$;
CREATE TRIGGER current_review BEFORE INSERT ON operator_review FOR EACH ROW EXECUTE FUNCTION guard_current_review();
COMMIT;
