BEGIN;
INSERT INTO ticket_ref(ticket_id, external_ref) VALUES ('00000000-0000-0000-0000-000000000001', 'DEMO-SQL');
INSERT INTO analysis_proposal(proposal_id,ticket_id,request_id,kb_version,status,mode,category,urgency,urgency_reason,summary,next_step)
VALUES ('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001','DEMO-SQL','demo','not_analyzed','escalate','other','unassessed','stub','stub','manual');
DO $$
BEGIN
  BEGIN
    INSERT INTO operator_review(review_id,proposal_id,actor_ref,decision,action,final_text)
    VALUES ('00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000002','demo','accepted','send_answer','demo');
    RAISE EXCEPTION 'Sending without confirmation was accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    INSERT INTO operator_review(review_id,proposal_id,actor_ref,decision,action,confirmed_at)
    VALUES ('00000000-0000-0000-0000-000000000004','00000000-0000-0000-0000-000000000002','demo','accepted','escalate',now());
    RAISE EXCEPTION 'Escalation without destination was accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    INSERT INTO operator_review(review_id,proposal_id,actor_ref,decision,action)
    VALUES ('00000000-0000-0000-0000-000000000005','00000000-0000-0000-0000-000000000002','demo','rejected','none');
    RAISE EXCEPTION 'Rejection without reason was accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    UPDATE analysis_proposal SET needs_human_review=false;
    RAISE EXCEPTION 'Human review false was accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;
INSERT INTO operator_review(review_id,proposal_id,actor_ref,decision,action,reason)
VALUES ('00000000-0000-0000-0000-000000000006','00000000-0000-0000-0000-000000000002','demo','rejected','none','Synthetic test');
ROLLBACK;
