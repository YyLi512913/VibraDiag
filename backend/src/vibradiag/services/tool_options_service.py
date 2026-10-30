"""Scoped references for the minimal workbench; engineering maintenance stays controlled."""
from sqlalchemy import text
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services.measurement_service import fresh, authorized_measurement


def options(measurement_id,principal):
    with transaction() as connection:
        current=fresh(connection,principal)
        record=authorized_measurement(connection,current,measurement_id)
        revision=connection.execute(text('SELECT * FROM measurement_revisions WHERE id=:id AND organization_id=:org'),
            dict(id=record['current_revision_id'],org=current.organization_id)).mappings().one()
        contexts=connection.execute(text('''SELECT c.id,c.bearing_revision_id,c.checked_at,
            a.name AS component_name,(a.current_bearing_revision_id=c.bearing_revision_id) AS is_current_bearing_revision
            FROM diagnostic_contexts c JOIN asset_components a ON a.id=c.component_id AND a.organization_id=c.organization_id
            WHERE c.organization_id=:org AND c.measurement_revision_id=:revision AND a.status='active'
            ORDER BY c.checked_at DESC,c.id LIMIT 100'''),dict(org=current.organization_id,revision=revision['id'])).mappings().all()
        profiles=connection.execute(text('''SELECT id,tool_version,configuration,qualification_status FROM tool_profiles
            WHERE organization_id=:org AND asset_id=:asset
            AND (configuration->>'sampling_rate_hz')::float8=:rate ORDER BY created_at DESC,id LIMIT 100'''),
            dict(org=current.organization_id,asset=revision['asset_id'],rate=revision['sampling_rate_hz'])).mappings().all()
        return dict(measurement_revision_id=str(revision['id']),contexts=[dict(c) for c in contexts],profiles=[dict(p) for p in profiles],max_options=100)
