"""Identity primitives and resource predicates shared by HTTP and administration."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import secrets
import unicodedata
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import text

# RFC 9106 low-memory profile: explicit parameters, independent of future defaults.
password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4,
                                 hash_len=32, salt_len=16)
_dummy_hash = password_hasher.hash(secrets.token_urlsafe(32))
ROLES = {'inspector', 'engineer', 'supervisor', 'admin'}


def normalized_username(value):
    name = unicodedata.normalize('NFKC', value).strip().casefold()
    if not 1 <= len(name) <= 128:
        raise ValueError('Invalid username length')
    return name


def digest(value):
    return sha256(value.encode('utf8')).hexdigest()


def audit(connection, organization_id, actor_id, action, object_type, object_id=None, request_id=None):
    connection.execute(text('''INSERT INTO audit_events
        (id,organization_id,actor_id,action,object_type,object_id,request_id)
        VALUES (:id,:org,:actor,:action,:type,:object,:request)'''),
        dict(id=uuid4(), org=organization_id, actor=actor_id, action=action,
             type=object_type, object=object_id, request=request_id or uuid4()))


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    organization_id: UUID
    session_id: UUID
    roles: frozenset[str]
    csrf_hash: str


def login(connection, organization_id, username, password, request_id):
    try:
        name=normalized_username(username)
    except ValueError:
        name=''
    row = connection.execute(text('''SELECT u.* FROM users u JOIN organizations o
        ON o.id=u.organization_id WHERE u.organization_id=:org
        AND u.username_normalized=:name AND u.status='active' AND o.status='active'
        FOR UPDATE OF u'''), dict(org=organization_id, name=name)).mappings().first()
    try:
        valid = password_hasher.verify(row['password_hash'] if row else _dummy_hash, password)
    except (VerificationError, InvalidHashError):
        valid = False
    if not row or not valid:
        return None
    if password_hasher.check_needs_rehash(row['password_hash']):
        connection.execute(text('UPDATE users SET password_hash=:hash WHERE id=:id'),
                           dict(hash=password_hasher.hash(password), id=row['id']))
    token, csrf, session_id = secrets.token_urlsafe(32), secrets.token_urlsafe(32), uuid4()
    connection.execute(text('''INSERT INTO auth_sessions
        (id,user_id,organization_id,token_hash,csrf_hash,expires_at)
        VALUES (:id,:user,:org,:token,:csrf,:expiry)'''),
        dict(id=session_id, user=row['id'], org=organization_id, token=digest(token),
             csrf=digest(csrf), expiry=datetime.now(timezone.utc)+timedelta(hours=8)))
    audit(connection, organization_id, row['id'], 'auth.login', 'session', session_id, request_id)
    return token, csrf


def authenticate(connection, token):
    if not token or len(token)>256:
        return None
    row = connection.execute(text('''SELECT s.* FROM auth_sessions s
        JOIN users u ON u.id=s.user_id AND u.organization_id=s.organization_id
        JOIN organizations o ON o.id=s.organization_id
        WHERE s.token_hash=:token AND s.revoked_at IS NULL AND s.expires_at>now()
        AND u.status='active' AND o.status='active' '''), dict(token=digest(token))).mappings().first()
    if not row:
        return None
    roles = connection.execute(text('SELECT role FROM role_assignments WHERE user_id=:id AND organization_id=:org'),
                               dict(id=row['user_id'], org=row['organization_id'])).scalars()
    return Principal(row['user_id'], row['organization_id'], row['id'], frozenset(roles), row['csrf_hash'])


def valid_csrf(principal, token):
    return bool(token and len(token)<=256 and hmac.compare_digest(principal.csrf_hash, digest(token)))


def refresh_principal(connection, principal):
    """Recheck a previously authenticated identity after potentially slow file IO."""
    row=connection.execute(text('''SELECT s.csrf_hash FROM auth_sessions s
        JOIN users u ON u.id=s.user_id AND u.organization_id=s.organization_id
        JOIN organizations o ON o.id=s.organization_id
        WHERE s.id=:session AND s.user_id=:user AND s.organization_id=:org
        AND s.revoked_at IS NULL AND s.expires_at>now() AND u.status='active' AND o.status='active' '''),
        dict(session=principal.session_id,user=principal.user_id,org=principal.organization_id)).mappings().first()
    if not row:
        return None
    roles=connection.execute(text('SELECT role FROM role_assignments WHERE user_id=:user AND organization_id=:org'),
        dict(user=principal.user_id,org=principal.organization_id)).scalars()
    return Principal(principal.user_id,principal.organization_id,principal.session_id,frozenset(roles),row['csrf_hash'])


def asset_allowed(connection, principal, asset_id, *, write=False):
    if write and 'engineer' not in principal.roles:
        return False
    field = 'can_write' if write else 'can_read'
    return bool(connection.execute(text(f'''SELECT 1 FROM assets a JOIN asset_grants g
        ON g.asset_id=a.id AND g.organization_id=a.organization_id
        WHERE a.id=:id AND a.organization_id=:org AND a.status='active'
        AND g.user_id=:user AND g.{field}'''),
        dict(id=asset_id, org=principal.organization_id, user=principal.user_id)).first())


def measurement_allowed(connection, principal, measurement_id, *, write=False):
    if write and not principal.roles.intersection({'inspector','engineer'}):
        return False
    field = 'can_write' if write else 'can_read'
    row = connection.execute(text(f'''SELECT m.owner_id,r.asset_id,
        EXISTS(SELECT 1 FROM measurement_grants g WHERE g.measurement_id=m.id
        AND g.organization_id=m.organization_id AND g.user_id=:user AND g.{field}) AS granted
        FROM measurements m LEFT JOIN measurement_revisions r
        ON r.id=m.current_revision_id AND r.organization_id=m.organization_id
        WHERE m.id=:id AND m.organization_id=:org AND m.state='ready' '''),
        dict(id=measurement_id, org=principal.organization_id, user=principal.user_id)).mappings().first()
    if not row or not (row['owner_id']==principal.user_id or row['granted']):
        return False
    # Explicit grants to someone else's record require an engineering role for writes.
    if write and row['owner_id']!=principal.user_id and 'engineer' not in principal.roles:
        return False
    if row['asset_id'] is not None:
        # Inspectors can write their own input with explicit asset write scope.
        if write:
            return bool(connection.execute(text('''SELECT 1 FROM asset_grants g JOIN assets a
                ON a.id=g.asset_id AND a.organization_id=g.organization_id
                WHERE g.user_id=:user AND g.asset_id=:asset AND g.organization_id=:org
                AND g.can_write AND a.status='active' '''),
                dict(user=principal.user_id, asset=row['asset_id'], org=principal.organization_id)).first())
        return asset_allowed(connection, principal, row['asset_id'])
    return True
