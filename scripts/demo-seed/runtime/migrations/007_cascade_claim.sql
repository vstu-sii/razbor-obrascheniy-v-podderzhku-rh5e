ALTER TABLE model_attempt ADD COLUMN invocation_claimed INTEGER NOT NULL DEFAULT 0 CHECK(invocation_claimed IN (0,1));
CREATE TRIGGER automatic_review_guard BEFORE INSERT ON operator_review
WHEN EXISTS (SELECT 1 FROM analysis_proposal WHERE proposal_id=NEW.proposal_id
             AND json_extract(card_json,'$.needs_human_review')=0)
BEGIN
 SELECT RAISE(ABORT, 'automatic response cannot have an operator review');
END;
