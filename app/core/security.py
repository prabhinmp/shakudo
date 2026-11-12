"""Authentication (who are you?) and authorization (what may you do?).

Token claims we require:  sub, iss, aud, exp, role, namespaces
  role        viewer < operator < admin
  namespaces  list of allowed namespace names; trailing-* wildcards allowed ("team-*")
"""
from dataclasses import dataclass
from fnmatch import fnmatchcase

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, is_protected_namespace
from app.errors import AuthError, ForbiddenError, RateLimitError

ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}
ALGORITHM = "HS256"  # pinned: never read the algorithm from the token header

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    sub: str
    role: str
    namespaces: tuple[str, ...]


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def decode_token(token: str, settings: Settings) -> Principal:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "iss", "aud"]},
        )
    except jwt.PyJWTError:
        # Same message for every failure: don't tell attackers *why* it failed.
        raise AuthError("Invalid or expired token")

    role = claims.get("role")
    namespaces = claims.get("namespaces")
    if role not in ROLE_RANK or not isinstance(namespaces, list) or not all(isinstance(n, str) for n in namespaces):
        raise AuthError("Invalid or expired token")
    return Principal(sub=str(claims["sub"]), role=role, namespaces=tuple(namespaces))


def get_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings_dep),
) -> Principal:
    limiters = request.app.state.limiters
    # NOTE: behind a proxy/ingress this is the proxy's IP unless the proxy is trusted
    # to set it (we deliberately do NOT trust X-Forwarded-For: it is spoofable).
    ip = request.client.host if request.client else "unknown"

    retry = limiters.auth_fail.blocked_for(ip)
    if retry:
        raise RateLimitError(retry)
    try:
        if creds is None:
            raise AuthError("Missing bearer token")
        principal = decode_token(creds.credentials, settings)
    except AuthError:
        limiters.auth_fail.record(ip)
        raise
    request.state.principal = principal  # lets the error handler name the actor in audit logs
    return principal


def require_role(minimum: str):
    """Build a FastAPI dependency that enforces a minimum role."""

    def checker(request: Request, principal: Principal = Depends(get_principal)) -> Principal:
        # Rate limit first, so forbidden attempts are counted too. Writes get a tighter budget.
        limiters = request.app.state.limiters
        limiter = limiters.read if request.method == "GET" else limiters.write
        retry = limiter.hit(principal.sub)
        if retry:
            raise RateLimitError(retry)
        if ROLE_RANK[principal.role] < ROLE_RANK[minimum]:
            raise ForbiddenError(f"Role '{principal.role}' may not perform this action")
        return principal

    return checker


def is_namespace_denied(namespace: str, settings: Settings) -> bool:
    return is_protected_namespace(namespace, settings.denied_namespaces)


def namespace_allowed(principal: Principal, namespace: str, settings: Settings) -> bool:
    if is_namespace_denied(namespace, settings):
        return False
    return any(fnmatchcase(namespace, pattern) for pattern in principal.namespaces)


def namespace_readable(principal: Principal, namespace: str) -> bool:
    """READ access: only the token's namespace list matters. Built-in namespaces (default, kube-*)
    are visible read-only; writes still go through namespace_allowed, which refuses them."""
    return any(fnmatchcase(namespace, pattern) for pattern in principal.namespaces)


def authorize_namespace_read(principal: Principal, namespace: str) -> None:
    if not namespace_readable(principal, namespace):
        raise ForbiddenError(
            f"Namespace '{namespace}' is not in this token's allowed namespaces ({', '.join(principal.namespaces) or 'none'})")


def authorize_namespace(principal: Principal, namespace: str, settings: Settings) -> None:
    # Two different reasons, so say which one: a built-in namespace can never be used by anyone,
    # whereas a namespace outside the token's list just needs a token that includes it.
    if is_namespace_denied(namespace, settings):
        raise ForbiddenError(f"Namespace '{namespace}' is protected (built-in Kubernetes namespace) and cannot be used")
    if not namespace_allowed(principal, namespace, settings):
        raise ForbiddenError(
            f"Namespace '{namespace}' is not in this token's allowed namespaces ({', '.join(principal.namespaces) or 'none'}); "
            "use a token whose 'namespaces' claim includes it, or '*' for all non-protected namespaces")