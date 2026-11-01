ALTER TABLE daily_usage ADD COLUMN model_calls INTEGER NOT NULL DEFAULT 0 CHECK(model_calls>=0);
