BEGIN;

-- Running upgrade 0003_add_walk_path -> 0004_community

CREATE TABLE activity_events (
    id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    kind VARCHAR(32) NOT NULL, 
    payload JSON, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    CONSTRAINT pk_activity_events PRIMARY KEY (id), 
    CONSTRAINT fk_activity_events_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_activity_events_created_at ON activity_events (created_at);

CREATE INDEX ix_activity_events_user_id ON activity_events (user_id);

CREATE TABLE challenges (
    id UUID NOT NULL, 
    creator_id UUID, 
    title VARCHAR(200) NOT NULL, 
    metric VARCHAR(16) NOT NULL, 
    goal INTEGER NOT NULL, 
    scope VARCHAR(16) NOT NULL, 
    starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    ends_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    CONSTRAINT pk_challenges PRIMARY KEY (id), 
    CONSTRAINT fk_challenges_creator_id_users FOREIGN KEY(creator_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_challenges_creator_id ON challenges (creator_id);

CREATE TABLE friendships (
    id UUID NOT NULL, 
    requester_id UUID NOT NULL, 
    addressee_id UUID NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    responded_at TIMESTAMP WITH TIME ZONE, 
    CONSTRAINT pk_friendships PRIMARY KEY (id), 
    CONSTRAINT fk_friendships_addressee_id_users FOREIGN KEY(addressee_id) REFERENCES users (id) ON DELETE CASCADE, 
    CONSTRAINT fk_friendships_requester_id_users FOREIGN KEY(requester_id) REFERENCES users (id) ON DELETE CASCADE, 
    CONSTRAINT uq_friendships_requester_id UNIQUE (requester_id, addressee_id)
);

CREATE INDEX ix_friendships_addressee_id ON friendships (addressee_id);

CREATE INDEX ix_friendships_requester_id ON friendships (requester_id);

CREATE TABLE challenge_participants (
    id UUID NOT NULL, 
    challenge_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    joined_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    progress INTEGER NOT NULL, 
    CONSTRAINT pk_challenge_participants PRIMARY KEY (id), 
    CONSTRAINT fk_challenge_participants_challenge_id_challenges FOREIGN KEY(challenge_id) REFERENCES challenges (id) ON DELETE CASCADE, 
    CONSTRAINT fk_challenge_participants_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    CONSTRAINT uq_challenge_participants_challenge_id UNIQUE (challenge_id, user_id)
);

CREATE INDEX ix_challenge_participants_challenge_id ON challenge_participants (challenge_id);

CREATE INDEX ix_challenge_participants_user_id ON challenge_participants (user_id);

ALTER TABLE users ADD COLUMN handle VARCHAR(32);

ALTER TABLE users ADD COLUMN avatar_url TEXT;

ALTER TABLE users ADD COLUMN last_active_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE users ADD CONSTRAINT uq_users_handle UNIQUE (handle);

UPDATE alembic_version SET version_num='0004_community' WHERE alembic_version.version_num = '0003_add_walk_path';

COMMIT;

