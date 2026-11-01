BEGIN;
INSERT INTO ticket_ref(ticket_id,external_ref) VALUES ('00000000-0000-0000-0000-000000000001','test-a'),('00000000-0000-0000-0000-000000000002','test-b');
INSERT INTO analysis_event(event_id,ticket_id,input_version,event_type,kb_version,occurred_at)
VALUES ('event-a','00000000-0000-0000-0000-000000000001',1,'ticket.created','v1',now());
INSERT INTO analysis_job(job_id,ticket_id,input_version,event_id,state)
VALUES ('00000000-0000-0000-0000-000000000010','00000000-0000-0000-0000-000000000001',1,'event-a','queued');
DO $$ BEGIN
 BEGIN
  INSERT INTO analysis_job(job_id,ticket_id,input_version,event_id,state)
  VALUES ('00000000-0000-0000-0000-000000000011','00000000-0000-0000-0000-000000000002',1,'event-a','queued');
  RAISE EXCEPTION 'cross-ticket event accepted';
 EXCEPTION WHEN foreign_key_violation THEN NULL; END;
 BEGIN
  INSERT INTO analysis_event(event_id,ticket_id,input_version,event_type,kb_version,occurred_at)
  VALUES ('event-duplicate','00000000-0000-0000-0000-000000000001',1,'ticket.created','v1',now());
  RAISE EXCEPTION 'duplicate version accepted';
 EXCEPTION WHEN unique_violation THEN NULL; END;
 BEGIN
  INSERT INTO model_attempt(job_id,attempt_no,request_id,reserved_tokens,state)
  VALUES ('00000000-0000-0000-0000-000000000010',3,'00000000-0000-0000-0000-000000000020',10,'reserved');
  RAISE EXCEPTION 'third attempt accepted';
 EXCEPTION WHEN check_violation THEN NULL; END;
END $$;
INSERT INTO analysis_proposal(proposal_id,ticket_id,mode,category,summary,model_version,input_version)
VALUES ('00000000-0000-0000-0000-000000000030','00000000-0000-0000-0000-000000000001','escalate','other','test','stub',1);
INSERT INTO operator_review(review_id,proposal_id,actor_ref,decision,reason)
VALUES ('00000000-0000-0000-0000-000000000040','00000000-0000-0000-0000-000000000030','test','accepted','test');
UPDATE ticket_ref SET current_input_version=2 WHERE external_ref='test-a';
DO $$ BEGIN
 BEGIN
  INSERT INTO operator_review(review_id,proposal_id,actor_ref,decision,reason)
  VALUES ('00000000-0000-0000-0000-000000000041','00000000-0000-0000-0000-000000000030','test','accepted','test');
  RAISE EXCEPTION 'stale review accepted';
 EXCEPTION WHEN check_violation THEN NULL; END;
END $$;
ROLLBACK;
