ALTER TABLE model_attempt ADD COLUMN invocation_claimed INTEGER NOT NULL DEFAULT 0 CHECK(invocation_claimed IN (0,1));
CREATE FUNCTION reject_automatic_review() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS (SELECT 1 FROM analysis_proposal WHERE proposal_id=NEW.proposal_id
            AND (card_json::jsonb ->> 'needs_human_review')='false') THEN
  RAISE EXCEPTION 'automatic response cannot have an operator review' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER automatic_review_guard BEFORE INSERT ON operator_review
FOR EACH ROW EXECUTE FUNCTION reject_automatic_review();
