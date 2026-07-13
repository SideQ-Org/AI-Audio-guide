BEGIN;

-- Running upgrade 0006_walk_shared -> 0007_walk_summary
-- Structured end-of-walk recap, kept so it's readable later in the walk detail (owner + friend).

ALTER TABLE walks ADD COLUMN summary TEXT;

UPDATE alembic_version SET version_num='0007_walk_summary' WHERE alembic_version.version_num = '0006_walk_shared';

COMMIT;
