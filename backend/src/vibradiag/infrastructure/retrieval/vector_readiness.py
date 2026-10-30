"""Read-only PostgreSQL capability inspection. Never installs extensions or changes grants."""
from sqlalchemy import text


def inspect_vector(connection):
    row = connection.execute(text('''SELECT current_setting('server_version') AS postgresql_version,
        (SELECT default_version FROM pg_available_extensions WHERE name='vector') AS available_version,
        (SELECT v.superuser FROM pg_available_extension_versions v JOIN pg_available_extensions e
          ON e.name=v.name AND e.default_version=v.version WHERE v.name='vector') AS installation_requires_superuser,
        (SELECT v.trusted FROM pg_available_extension_versions v JOIN pg_available_extensions e
          ON e.name=v.name AND e.default_version=v.version WHERE v.name='vector') AS installation_trusted,
        (SELECT extversion FROM pg_extension WHERE extname='vector') AS installed_version,
        (SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='vector') AS extension_schema,
        r.rolsuper AS superuser, r.rolcreatedb AS create_database, r.rolcreaterole AS create_role,
        has_schema_privilege(current_user,'public','CREATE') AS public_ddl
        FROM pg_roles r WHERE r.rolname=current_user''')).mappings().one()
    result = dict(row)
    result['ready_for_vector_migration'] = bool(row['installed_version'])
    result['extension_modified'] = False
    result['blockers'] = ([] if row['installed_version'] else [
        'vector_extension_not_installed' if row['available_version'] else 'vector_extension_not_available'])
    return result
