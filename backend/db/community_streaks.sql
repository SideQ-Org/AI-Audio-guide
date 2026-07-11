BEGIN;

-- Running upgrade 0004_community -> 0005_group_streaks

CREATE TABLE group_streaks (
    id UUID NOT NULL, 
    creator_id UUID NOT NULL, 
    title VARCHAR(120), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    CONSTRAINT pk_group_streaks PRIMARY KEY (id), 
    CONSTRAINT fk_group_streaks_creator_id_users FOREIGN KEY(creator_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_group_streaks_creator_id ON group_streaks (creator_id);

CREATE TABLE group_streak_members (
    id UUID NOT NULL, 
    streak_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    joined_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    CONSTRAINT pk_group_streak_members PRIMARY KEY (id), 
    CONSTRAINT fk_group_streak_members_streak_id_group_streaks FOREIGN KEY(streak_id) REFERENCES group_streaks (id) ON DELETE CASCADE, 
    CONSTRAINT fk_group_streak_members_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    CONSTRAINT uq_group_streak_members_streak_id UNIQUE (streak_id, user_id)
);

CREATE INDEX ix_group_streak_members_streak_id ON group_streak_members (streak_id);

CREATE INDEX ix_group_streak_members_user_id ON group_streak_members (user_id);

UPDATE alembic_version SET version_num='0005_group_streaks' WHERE alembic_version.version_num = '0004_community';

COMMIT;

