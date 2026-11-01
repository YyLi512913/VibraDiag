"""Reject null metadata tags instead of allowing SQL CHECK's unknown result."""
from alembic import op
revision='0023_diagnostics_null_guard'
down_revision='0022_model_diagnostics'


def upgrade():
    op.get_bind().exec_driver_sql("""ALTER TABLE agent_model_rounds DROP CONSTRAINT model_validation_diagnostics_check;
    ALTER TABLE agent_model_rounds ADD CONSTRAINT model_validation_diagnostics_check CHECK (
      validation_diagnostics IS NULL OR COALESCE((
        state='failed' AND error_code='MODEL_OUTPUT_SCHEMA_INVALID'
        AND octet_length(validation_diagnostics::text)<=4096
        AND jsonb_typeof(validation_diagnostics)='object'
        AND validation_diagnostics->>'version'='model-validation-diagnostics-v1'
        AND validation_diagnostics ?& ARRAY['version','top_level_type','action_kind','known_fields','unknown_field_count','error_count','issues','truncated']
        AND (validation_diagnostics-ARRAY['version','top_level_type','action_kind','known_fields','unknown_field_count','error_count','issues','truncated'])='{}'::jsonb
        AND CASE WHEN jsonb_typeof(validation_diagnostics->'issues')='array' THEN jsonb_array_length(validation_diagnostics->'issues') BETWEEN 1 AND 8 ELSE false END
      ),false));""")


def downgrade():raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
