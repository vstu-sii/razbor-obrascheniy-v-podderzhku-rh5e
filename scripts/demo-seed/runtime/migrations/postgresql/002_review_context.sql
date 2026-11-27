-- Preserve review/context fields already present in the teacher branch SQLite schema.
ALTER TABLE analysis_proposal ADD COLUMN input_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE operator_review ADD COLUMN destination TEXT;
ALTER TABLE operator_review ADD COLUMN confirmed_at TIMESTAMPTZ;
ALTER TABLE operator_review ADD CONSTRAINT reason_nonempty CHECK(length(trim(reason)) > 0) NOT VALID;
ALTER TABLE operator_review ADD CONSTRAINT review_confirmation CHECK(decision='rejected' OR confirmed_at IS NOT NULL) NOT VALID;
ALTER TABLE operator_review ADD CONSTRAINT edited_text CHECK(decision<>'edited' OR (final_text IS NOT NULL AND length(trim(final_text))>0)) NOT VALID;
