"""Local administrator CLI. Passwords never appear in command arguments."""
import argparse
import getpass
import os
from uuid import UUID, uuid4
from sqlalchemy import text
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services.identity_service import ROLES, audit, normalized_username, password_hasher


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    org=sub.add_parser('create-org'); org.add_argument('--name',required=True)
    user=sub.add_parser('create-user'); user.add_argument('--org',type=UUID,required=True)
    user.add_argument('--username',required=True); user.add_argument('--roles',nargs='+',choices=sorted(ROLES),required=True)
    status=sub.add_parser('set-user-status'); status.add_argument('--user',type=UUID,required=True)
    status.add_argument('--status',choices=['active','disabled'],required=True)
    asset=sub.add_parser('create-asset'); asset.add_argument('--actor',type=UUID,required=True)
    asset.add_argument('--code',required=True); asset.add_argument('--name',required=True); asset.add_argument('--type',required=True)
    grant=sub.add_parser('grant-asset'); grant.add_argument('--actor',type=UUID,required=True)
    grant.add_argument('--user',type=UUID,required=True);grant.add_argument('--asset',type=UUID,required=True)
    grant.add_argument('--access',choices=['read','write','none'],required=True)
    role=sub.add_parser('set-roles');role.add_argument('--user',type=UUID,required=True)
    role.add_argument('--roles',nargs='*',choices=sorted(ROLES),default=[])
    args=parser.parse_args()
    password=None
    if args.command=='create-user':
        password=os.environ.get('VIBRADIAG_INITIAL_PASSWORD') or getpass.getpass('New user password: ')
        if len(password)<12 or len(password)>1024:
            parser.error('Application password must contain 12–1024 characters')
    with transaction() as conn:
        if args.command=='create-org':
            identifier=uuid4()
            conn.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'),dict(id=identifier,name=args.name))
            audit(conn,identifier,None,'organization.create','organization',identifier)
        elif args.command=='create-user':
            identifier=uuid4()
            conn.execute(text('INSERT INTO users(id,organization_id,username_normalized,password_hash) VALUES (:id,:org,:name,:hash)'),
                dict(id=identifier,org=args.org,name=normalized_username(args.username),hash=password_hasher.hash(password)))
            for role in set(args.roles):
                conn.execute(text('INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:id,:org,:role,:id)'),
                             dict(id=identifier,org=args.org,role=role))
            audit(conn,args.org,identifier,'user.create.local_cli','user',identifier)
        elif args.command in {'set-user-status','set-roles'}:
            row=conn.execute(text('SELECT * FROM users WHERE id=:id FOR UPDATE'),dict(id=args.user)).mappings().one()
            identifier=args.user
            if args.command=='set-user-status':
                conn.execute(text('UPDATE users SET status=:status,row_version=row_version+1 WHERE id=:id'),dict(id=args.user,status=args.status))
            else:
                conn.execute(text('DELETE FROM role_assignments WHERE user_id=:id'),dict(id=args.user))
                for role in set(args.roles):
                    conn.execute(text('INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:id,:org,:role,:id)'),
                                 dict(id=args.user,org=row['organization_id'],role=role))
            conn.execute(text('UPDATE auth_sessions SET revoked_at=now() WHERE user_id=:id AND revoked_at IS NULL'),dict(id=args.user))
            audit(conn,row['organization_id'],None,args.command+'.local_cli','user',args.user)
        else:
            actor=conn.execute(text('SELECT * FROM users WHERE id=:id AND status=\'active\''),dict(id=args.actor)).mappings().one()
            allowed=conn.execute(text("SELECT 1 FROM role_assignments WHERE user_id=:id AND role='admin'"),dict(id=args.actor)).first()
            if not allowed:
                raise ValueError('Local asset administration requires an explicitly assigned admin actor')
            if args.command=='create-asset':
                identifier=uuid4()
                conn.execute(text('INSERT INTO assets(id,organization_id,asset_code,name,equipment_type) VALUES (:id,:org,:code,:name,:type)'),
                    dict(id=identifier,org=actor['organization_id'],code=args.code,name=args.name,type=args.type))
            else:
                identifier=args.asset
                conn.execute(text('''INSERT INTO asset_grants(organization_id,user_id,asset_id,can_read,can_write)
                    VALUES (:org,:user,:asset,:read,:write) ON CONFLICT(user_id,asset_id)
                    DO UPDATE SET can_read=EXCLUDED.can_read,can_write=EXCLUDED.can_write'''),
                    dict(org=actor['organization_id'],user=args.user,asset=args.asset,read=args.access!='none',write=args.access=='write'))
            audit(conn,actor['organization_id'],args.actor,args.command+'.local_cli','asset',identifier)
    print(f'{args.command}: {identifier}')


if __name__=='__main__':
    try:
        main()
    except Exception:
        # SQLAlchemy exceptions can contain bound password hashes and connection strings.
        raise SystemExit('Administration failed; check identifiers, constraints and database configuration') from None
