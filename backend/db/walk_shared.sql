BEGIN;

-- Running upgrade 0005_group_streaks -> 0006_walk_shared

ALTER TABLE walks ADD COLUMN shared BOOLEAN DEFAULT false NOT NULL;

UPDATE alembic_version SET version_num='0006_walk_shared' WHERE alembic_version.version_num = '0005_group_streaks';

COMMIT;

