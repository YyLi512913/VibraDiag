from uuid import UUID, uuid4
from collections import OrderedDict
from threading import Lock
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import text

from vibradiag.core.config import setting
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services.identity_service import authenticate, audit, login, valid_csrf

router = APIRouter()
COOKIE = 'vibradiag_session'
_attempts=OrderedDict()
_attempt_lock=Lock()


def check_login_limit(request):
    # M1 runs one process. A shared limiter is required before multiple workers.
    key=request.client.host if request.client else 'unknown'
    now=monotonic()
    with _attempt_lock:
        times=[t for t in _attempts.pop(key,[]) if now-t<60]
        if len(times)>=20:
            _attempts[key]=times
            raise HTTPException(429,'Too many login attempts',headers={'Retry-After':'60'})
        _attempts[key]=times+[now]
        while len(_attempts)>10000:
            _attempts.popitem(last=False)


class LoginRequest(BaseModel):
    organization_id: UUID
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024)


def current_principal(request: Request):
    with transaction() as connection:
        principal = authenticate(connection, request.cookies.get(COOKIE))
    if not principal:
        raise HTTPException(401, 'Authentication required')
    if request.method not in {'GET','HEAD','OPTIONS'} and not valid_csrf(principal, request.headers.get('x-csrf-token')):
        raise HTTPException(403, 'Invalid CSRF token')
    return principal


@router.post('/auth/login')
def sign_in(payload: LoginRequest, request: Request, response: Response):
    check_login_limit(request)
    # JSON-only prevents cross-site HTML form login. Reject cross-origin browser requests.
    origin = request.headers.get('origin')
    if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
        raise HTTPException(403, 'Cross-origin login denied')
    with transaction() as connection:
        result = login(connection, payload.organization_id, payload.username,
                       payload.password.get_secret_value(), request.state.request_id)
    if not result:
        raise HTTPException(401, 'Invalid credentials')
    token, csrf = result
    response.set_cookie(COOKIE, token, max_age=28800, httponly=True,
                        secure=setting('VIBRADIAG_COOKIE_SECURE','true').lower()=='true',
                        samesite='strict', path='/api/v1')
    response.headers['Cache-Control']='no-store'
    return {'csrf_token':csrf, 'expires_in':28800}


@router.get('/auth/me')
def me(response: Response, principal=Depends(current_principal)):
    response.headers['Cache-Control']='no-store'
    return {'user_id':principal.user_id, 'organization_id':principal.organization_id,
            'roles':sorted(principal.roles)}


@router.post('/auth/logout', status_code=204)
def sign_out(request: Request, principal=Depends(current_principal)):
    with transaction() as connection:
        connection.execute(text('UPDATE auth_sessions SET revoked_at=now() WHERE id=:id'),dict(id=principal.session_id))
        audit(connection, principal.organization_id, principal.user_id, 'auth.logout',
              'session', principal.session_id, request.state.request_id)
    response=Response(status_code=204)
    response.delete_cookie(COOKIE,path='/api/v1')
    return response
