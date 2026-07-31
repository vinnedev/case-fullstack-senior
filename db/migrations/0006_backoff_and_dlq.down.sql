DROP TABLE dead_letter_jobs;
ALTER TABLE jobs DROP COLUMN next_attempt_at;
