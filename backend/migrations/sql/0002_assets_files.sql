CREATE TABLE assets (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organizations(id),
 asset_code text NOT NULL, name text NOT NULL, equipment_type text NOT NULL,
 rated_rpm double precision CHECK(rated_rpm>0 AND rated_rpm<'Infinity'::float8),
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
 row_version integer NOT NULL DEFAULT 1 CHECK(row_version>0),
 UNIQUE(id,organization_id), UNIQUE(organization_id,asset_code)
);
CREATE TABLE measurement_points (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, asset_id uuid NOT NULL,
 point_code text NOT NULL, name text NOT NULL,
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
 row_version integer NOT NULL DEFAULT 1 CHECK(row_version>0),
 UNIQUE(id,organization_id), UNIQUE(id,asset_id,organization_id), UNIQUE(asset_id,point_code),
 FOREIGN KEY(asset_id,organization_id) REFERENCES assets(id,organization_id)
);
CREATE TABLE asset_grants (
 organization_id uuid NOT NULL, user_id uuid NOT NULL, asset_id uuid NOT NULL,
 can_read boolean NOT NULL, can_write boolean NOT NULL CHECK(NOT can_write OR can_read),
 PRIMARY KEY(user_id,asset_id),
 FOREIGN KEY(user_id,organization_id) REFERENCES users(id,organization_id),
 FOREIGN KEY(asset_id,organization_id) REFERENCES assets(id,organization_id)
);
CREATE TABLE file_objects (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, storage_key text NOT NULL UNIQUE,
 sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'), byte_size bigint NOT NULL CHECK(byte_size>0),
 state text NOT NULL CHECK(state IN ('ready','corrupt','missing','archived')),
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,organization_id),
 FOREIGN KEY(created_by,organization_id) REFERENCES users(id,organization_id)
);
