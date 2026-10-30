CREATE TABLE measurements (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, file_object_id uuid NOT NULL,
 owner_id uuid NOT NULL, original_filename text NOT NULL,
 current_revision_id uuid, state text NOT NULL CHECK(state IN ('ready','archived')),
 row_version integer NOT NULL DEFAULT 1 CHECK(row_version>0), uploaded_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,organization_id),
 FOREIGN KEY(file_object_id,organization_id) REFERENCES file_objects(id,organization_id),
 FOREIGN KEY(owner_id,organization_id) REFERENCES users(id,organization_id)
);
CREATE TABLE measurement_grants (
 organization_id uuid NOT NULL, measurement_id uuid NOT NULL, user_id uuid NOT NULL,
 can_read boolean NOT NULL, can_write boolean NOT NULL CHECK(NOT can_write OR can_read), granted_by uuid NOT NULL,
 PRIMARY KEY(measurement_id,user_id),
 FOREIGN KEY(measurement_id,organization_id) REFERENCES measurements(id,organization_id),
 FOREIGN KEY(user_id,organization_id) REFERENCES users(id,organization_id),
 FOREIGN KEY(granted_by,organization_id) REFERENCES users(id,organization_id)
);
CREATE TABLE measurement_revisions (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, measurement_id uuid NOT NULL,
 revision_no integer NOT NULL CHECK(revision_no>0), parent_id uuid,
 sampling_rate_hz double precision NOT NULL CHECK(sampling_rate_hz>0 AND sampling_rate_hz<'Infinity'::float8),
 acceleration_unit text NOT NULL CHECK(acceleration_unit IN ('g','m/s2')),
 asset_id uuid, measurement_point_id uuid,
 direction text NOT NULL DEFAULT 'unknown' CHECK(direction IN ('horizontal_radial','vertical_radial','axial','other','unknown')),
 direction_note text, rpm double precision CHECK(rpm>0 AND rpm<'Infinity'::float8),
 load_value double precision CHECK(load_value>'-Infinity'::float8 AND load_value<'Infinity'::float8),
 load_unit text, load_note text, acquired_at timestamptz,
 source_type text NOT NULL CHECK(source_type IN ('public_dataset','synthetic','field_upload','unknown')),
 source_note text, parameter_sources jsonb NOT NULL, reason text NOT NULL,
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 CHECK((load_value IS NULL)=(load_unit IS NULL)),
 CHECK(direction<>'other' OR length(trim(direction_note))>0 AND direction_note IS NOT NULL),
 CHECK(measurement_point_id IS NULL OR asset_id IS NOT NULL),
 CHECK(parent_id IS NULL OR parent_id<>id),
 UNIQUE(id,organization_id), UNIQUE(id,measurement_id,organization_id), UNIQUE(measurement_id,revision_no),
 FOREIGN KEY(measurement_id,organization_id) REFERENCES measurements(id,organization_id),
 FOREIGN KEY(parent_id,measurement_id,organization_id) REFERENCES measurement_revisions(id,measurement_id,organization_id),
 FOREIGN KEY(asset_id,organization_id) REFERENCES assets(id,organization_id),
 FOREIGN KEY(measurement_point_id,asset_id,organization_id) REFERENCES measurement_points(id,asset_id,organization_id),
 FOREIGN KEY(created_by,organization_id) REFERENCES users(id,organization_id)
);
ALTER TABLE measurements ADD CONSTRAINT measurements_current_revision_fk
 FOREIGN KEY(current_revision_id,id,organization_id) REFERENCES measurement_revisions(id,measurement_id,organization_id)
 DEFERRABLE INITIALLY DEFERRED;
CREATE TRIGGER revision_immutable BEFORE UPDATE OR DELETE ON measurement_revisions
 FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE TABLE input_confirmations (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, revision_id uuid NOT NULL, actor_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz,
 UNIQUE(revision_id,actor_id), UNIQUE(id,revision_id,organization_id),
 FOREIGN KEY(revision_id,organization_id) REFERENCES measurement_revisions(id,organization_id),
 FOREIGN KEY(actor_id,organization_id) REFERENCES users(id,organization_id)
);
