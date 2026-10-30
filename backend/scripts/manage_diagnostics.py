"""Controlled local engineering declarations; password is prompted, never an argument."""
import argparse
from getpass import getpass
import json
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import text
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services.identity_service import login, authenticate, audit
from vibradiag.services import diagnostic_context_service as service
from vibradiag.services.tool_profile_service import create_profile
from vibradiag.services.comparison_service import create_declaration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['component', 'bearing-revision', 'context', 'read-context', 'envelope-profile', 'comparison-declaration'])
    parser.add_argument('--organization-id', required=True, type=UUID)
    parser.add_argument('--username', required=True)
    parser.add_argument('--input', type=Path, help='Local JSON declaration (write operations)')
    parser.add_argument('--id', type=UUID, help='Context ID (read-context)')
    args = parser.parse_args()
    if (args.operation == 'read-context' and (not args.id or args.input)) or (args.operation != 'read-context' and (not args.input or args.id)):
        parser.error('Use --id for read-context; --input for writes')
    payload = None
    if args.input:
        if args.input.stat().st_size > 65536:
            parser.error('Declaration exceeds 64 KiB')
        payload = json.loads(args.input.read_text(encoding='utf-8-sig'))
    request_id = uuid4()
    with transaction() as connection:
        credentials = login(connection, args.organization_id, args.username, getpass('Password: '), request_id)
        if credentials is None:
            raise SystemExit('Authentication failed')
        principal = authenticate(connection, credentials[0])
    try:
        operations = {'component': service.create_component, 'bearing-revision': service.create_bearing_revision,
                      'context': service.create_context, 'envelope-profile': create_profile, 'comparison-declaration': create_declaration}
        result = service.read_context(args.id, principal) if args.operation == 'read-context' else {
            'id': str(operations[args.operation](payload, principal, request_id))}
        print(json.dumps(result, default=str, ensure_ascii=False, indent=2, allow_nan=False))
    finally:
        with transaction() as connection:
            connection.execute(text('UPDATE auth_sessions SET revoked_at=now() WHERE id=:id'), dict(id=principal.session_id))
            audit(connection, principal.organization_id, principal.user_id, 'auth.logout', 'session', principal.session_id, request_id)


if __name__ == '__main__':
    try:
        main()
    except ValidationError as error:
        # Do not echo supplied declaration contents or paths.
        raise SystemExit('Invalid declaration fields: ' + ', '.join('.'.join(map(str, item['loc'])) for item in error.errors())) from None
    except MeasurementError as error:
        raise SystemExit(error.code + ': ' + str(error)) from None
