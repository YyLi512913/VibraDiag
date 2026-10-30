"""Versioned target parameters and immutable per-input diagnostic context."""
from alembic import op

revision = '0009_diagnostic_contexts'
down_revision = '0008_legacy_imports'


def upgrade():
    op.get_bind().exec_driver_sql('''
CREATE TABLE asset_components (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, asset_id uuid NOT NULL,
 component_code text NOT NULL CHECK(length(component_code) BETWEEN 1 AND 128),
 name text NOT NULL CHECK(length(name) BETWEEN 1 AND 256),
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
 current_bearing_revision_id uuid,
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,organization_id), UNIQUE(id,asset_id,organization_id),
 UNIQUE(organization_id,asset_id,component_code),
 FOREIGN KEY(asset_id,organization_id) REFERENCES assets(id,organization_id),
 FOREIGN KEY(created_by,organization_id) REFERENCES users(id,organization_id)
);
CREATE TABLE bearing_revisions (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, component_id uuid NOT NULL,
 parent_id uuid, revision_no integer NOT NULL CHECK(revision_no>0),
 parameterization text NOT NULL CHECK(parameterization IN ('geometry','coefficients')),
 parameters jsonb NOT NULL CHECK(jsonb_typeof(parameters)='object'),
 reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 2000),
 checked_by uuid NOT NULL, checked_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,organization_id), UNIQUE(id,component_id,organization_id),
 UNIQUE(component_id,organization_id,revision_no),
 CHECK((revision_no=1 AND parent_id IS NULL) OR (revision_no>1 AND parent_id IS NOT NULL)),
 FOREIGN KEY(component_id,organization_id) REFERENCES asset_components(id,organization_id),
 FOREIGN KEY(parent_id,component_id,organization_id) REFERENCES bearing_revisions(id,component_id,organization_id),
 FOREIGN KEY(checked_by,organization_id) REFERENCES users(id,organization_id)
);
ALTER TABLE asset_components ADD CONSTRAINT component_current_bearing_fk
 FOREIGN KEY(current_bearing_revision_id,id,organization_id)
 REFERENCES bearing_revisions(id,component_id,organization_id);
ALTER TABLE measurement_revisions ADD CONSTRAINT revision_asset_identity
 UNIQUE(id,asset_id,organization_id);
CREATE TABLE diagnostic_contexts (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL,
 measurement_revision_id uuid NOT NULL, asset_id uuid NOT NULL,
 component_id uuid NOT NULL, bearing_revision_id uuid NOT NULL,
 conditions jsonb NOT NULL CHECK(jsonb_typeof(conditions)='object'),
 acquisition jsonb CHECK(acquisition IS NULL OR jsonb_typeof(acquisition)='object'),
 source_type text NOT NULL CHECK(source_type IN ('synthetic','public_dataset','field_upload','unknown')),
 source_reference jsonb NOT NULL CHECK(jsonb_typeof(source_reference)='object'),
 reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 2000),
 checked_by uuid NOT NULL, checked_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,organization_id),
 FOREIGN KEY(measurement_revision_id,asset_id,organization_id) REFERENCES measurement_revisions(id,asset_id,organization_id),
 FOREIGN KEY(component_id,asset_id,organization_id) REFERENCES asset_components(id,asset_id,organization_id),
 FOREIGN KEY(bearing_revision_id,component_id,organization_id) REFERENCES bearing_revisions(id,component_id,organization_id),
 FOREIGN KEY(checked_by,organization_id) REFERENCES users(id,organization_id),
 CHECK(NOT conditions ?| ARRAY['rpm','target','rpm_checked']),
 CHECK(acquisition IS NULL OR NOT acquisition ? 'checked')
);
CREATE INDEX diagnostic_context_input_idx ON diagnostic_contexts(organization_id,measurement_revision_id,checked_at,id);
CREATE TRIGGER bearing_revision_immutable BEFORE UPDATE OR DELETE ON bearing_revisions
 FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE TRIGGER diagnostic_context_immutable BEFORE UPDATE OR DELETE ON diagnostic_contexts
 FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE FUNCTION protect_component_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'Components must be deactivated, not deleted';
 END IF;
 IF (NEW.id,NEW.organization_id,NEW.asset_id,NEW.component_code,NEW.name,NEW.created_by,NEW.created_at)
  IS DISTINCT FROM (OLD.id,OLD.organization_id,OLD.asset_id,OLD.component_code,OLD.name,OLD.created_by,OLD.created_at) THEN
  RAISE EXCEPTION 'Component identity is immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER component_identity_immutable BEFORE UPDATE OR DELETE ON asset_components
 FOR EACH ROW EXECUTE FUNCTION protect_component_identity();
''')


def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
