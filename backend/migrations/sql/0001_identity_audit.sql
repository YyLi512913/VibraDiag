CREATE TABLE organizations (
 id uuid PRIMARY KEY, name text NOT NULL UNIQUE,
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE users (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organizations(id),
 username_normalized text NOT NULL CHECK(length(username_normalized) BETWEEN 1 AND 128),
 password_hash text NOT NULL, status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
 row_version integer NOT NULL DEFAULT 1 CHECK(row_version>0),
 UNIQUE(id,organization_id), UNIQUE(organization_id,username_normalized)
);
CREATE TABLE role_assignments (
 user_id uuid NOT NULL, organization_id uuid NOT NULL,
 role text NOT NULL CHECK(role IN ('inspector','engineer','supervisor','admin')),
 granted_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(user_id,role),
 FOREIGN KEY(user_id,organization_id) REFERENCES users(id,organization_id),
 FOREIGN KEY(granted_by,organization_id) REFERENCES users(id,organization_id)
);
CREATE TABLE auth_sessions (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, organization_id uuid NOT NULL,
 token_hash text NOT NULL UNIQUE CHECK(length(token_hash)=64),
 csrf_hash text NOT NULL CHECK(length(csrf_hash)=64),
 expires_at timestamptz NOT NULL, revoked_at timestamptz,
 FOREIGN KEY(user_id,organization_id) REFERENCES users(id,organization_id)
);
CREATE INDEX auth_sessions_user ON auth_sessions(user_id);
CREATE TABLE audit_events (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organizations(id),
 actor_id uuid, action text NOT NULL, object_type text NOT NULL,
 object_id uuid, object_version integer, request_id uuid NOT NULL,
 timestamp timestamptz NOT NULL DEFAULT now(), details jsonb NOT NULL DEFAULT '{}',
 FOREIGN KEY(actor_id,organization_id) REFERENCES users(id,organization_id)
);
CREATE FUNCTION reject_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable history'; END $$;
CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON audit_events
 FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
