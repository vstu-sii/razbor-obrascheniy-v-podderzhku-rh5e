-- Forward-only adaptation of the LAB02 input/proposal/review boundary for SQLite.
ALTER TABLE analysis_proposal ADD COLUMN input_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE operator_review ADD COLUMN destination TEXT;
ALTER TABLE operator_review ADD COLUMN confirmed_at TEXT;

-- Preserve legacy rows; require valid decisions for all new inserts.
CREATE TRIGGER validate_review BEFORE INSERT ON operator_review
BEGIN
  SELECT CASE WHEN length(trim(NEW.reason)) = 0 THEN RAISE(ABORT, 'review reason required') END;
  SELECT CASE WHEN NEW.decision = 'edited' AND (NEW.final_text IS NULL OR length(trim(NEW.final_text)) = 0)
    THEN RAISE(ABORT, 'edited text required') END;
  SELECT CASE WHEN NEW.decision <> 'rejected' AND NEW.confirmed_at IS NULL
    THEN RAISE(ABORT, 'confirmation required') END;
  SELECT CASE WHEN NEW.destination IS NOT NULL AND NEW.destination NOT IN ('billing','technical','access','general')
    THEN RAISE(ABORT, 'invalid queue') END;
  SELECT CASE WHEN NEW.decision = 'rejected' AND NEW.destination IS NOT NULL
    THEN RAISE(ABORT, 'rejection has no destination') END;
  SELECT CASE WHEN NEW.decision <> 'rejected'
    AND json_extract((SELECT card_json FROM analysis_proposal WHERE proposal_id=NEW.proposal_id),'$.mode') = 'escalate'
    AND NEW.destination IS NULL THEN RAISE(ABORT, 'queue required') END;
END;
CREATE TRIGGER immutable_proposal BEFORE UPDATE ON analysis_proposal
BEGIN
  SELECT RAISE(ABORT, 'proposal is immutable');
END;
