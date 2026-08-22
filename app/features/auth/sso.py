"""Identidad SSO heredada de Databricks Apps (doc 38).

El navegador solo habla con la app FRONT (un solo origen, doc 36); el proxy SSO
de Databricks autentica ahí al usuario e inyecta `X-Forwarded-Email` /
`X-Forwarded-Preferred-Username` / `X-Forwarded-User`. El `server.mjs` del
front releva esa identidad hacia este backend en headers propios `x-dmh-sso-*`
acompañados de `x-dmh-proxy-secret` (secreto compartido entre las dos apps).

El secreto es lo que hace confiable el relay: el grupo `users` tiene CAN_USE
sobre la app backend, así que cualquier usuario del workspace puede golpearla
directo con curl y headers forjados — sin el secreto, esos headers se ignoran.
No se confía en ningún comportamiento no documentado del proxy de Databricks
(si sobrescribe o no `X-Forwarded-*` entrantes).

Resolución del nombre: los headers de Apps NO traen display name → SCIM del
workspace best-effort (mismo `WorkspaceClient` que Lakebase) con fallback al
`preferred_username` (si no es un correo) y al local-part del correo. Nunca
bloquea el login.
"""
from __future__ import annotations

import asyncio
import hmac
import re
from typing import NamedTuple

from fastapi import HTTPException, status
from starlette.requests import Request

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("app.auth.sso")

# Headers del relay (los pone el server.mjs del front; ver §4.1 del doc 38).
RELAY_SECRET_HEADER = "x-dmh-proxy-secret"
RELAY_EMAIL_HEADER = "x-dmh-sso-email"
RELAY_USERNAME_HEADER = "x-dmh-sso-username"
RELAY_USER_ID_HEADER = "x-dmh-sso-user-id"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_email(value: str) -> bool:
    """¿Parece un correo? (validación de forma, no de existencia). Puro."""
    return bool(_EMAIL_RE.match(value or ""))

# Timeout del lookup SCIM (best-effort): si el workspace no responde rápido,
# el login sigue con el nombre derivado del correo.
_SCIM_TIMEOUT_S = 3.0


class SsoIdentity(NamedTuple):
    """Identidad atestada por el SSO de Databricks, ya normalizada."""

    email: str                    # lowercase, trim — es el username de la sesión
    username_hint: str | None    # X-Forwarded-Preferred-Username (candidato a nombre)
    user_id: str | None          # X-Forwarded-User (solo para auditoría)


def _clean(value: str | None) -> str:
    return (value or "").strip()


def resolve_sso_identity(request: Request) -> SsoIdentity:
    """Deriva la identidad SSO de la request o levanta HTTPException.

    Con `PROXY_SHARED_SECRET` configurado (producción): exige el header del
    secreto (comparación constant-time) y lee SOLO los headers de relay.
    Sin secreto: 503 si `REQUIRE_AUTH` (fail-closed); en dev/tests acepta los
    headers de relay, los `X-Forwarded-*` directos o la simulación
    `LOCAL_DEV_USER` — el botón "Continue with Databricks" funciona en local
    sin configurar nada."""
    h = request.headers
    secret = settings.PROXY_SHARED_SECRET
    if secret:
        provided = _clean(h.get(RELAY_SECRET_HEADER))
        if not hmac.compare_digest(provided.encode(), secret.encode()):
            # 401 genérico: no distinguir "secreto malo" de "sin identidad".
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="SSO identity not available. Open the app through its "
                       "web address and try again.",
            )
        email = _clean(h.get(RELAY_EMAIL_HEADER)).lower()
        if not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Databricks did not forward an email for this session.",
            )
        return SsoIdentity(
            email=email,
            username_hint=_clean(h.get(RELAY_USERNAME_HEADER)) or None,
            user_id=_clean(h.get(RELAY_USER_ID_HEADER)) or None,
        )
    if settings.REQUIRE_AUTH:
        # Producción sin secreto compartido: NUNCA aceptar headers a ciegas.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SSO sign-in is not configured (PROXY_SHARED_SECRET is "
                   "missing). Administrator sign-in is still available.",
        )
    # Dev / tests (REQUIRE_AUTH=false): relay > X-Forwarded-* > simulación.
    email = (
        _clean(h.get(RELAY_EMAIL_HEADER))
        or _clean(h.get("X-Forwarded-Email"))
        or _clean(settings.LOCAL_DEV_USER)
    ).lower()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No SSO identity in this request.",
        )
    hint = (
        _clean(h.get(RELAY_USERNAME_HEADER))
        or _clean(h.get("X-Forwarded-Preferred-Username"))
        or _clean(settings.LOCAL_DEV_DISPLAY_NAME)
        or None
    )
    user_id = _clean(h.get(RELAY_USER_ID_HEADER)) or _clean(h.get("X-Forwarded-User")) or None
    return SsoIdentity(email=email, username_hint=hint, user_id=user_id)


# ── Nombre para mostrar (best-effort, nunca bloquea el login) ───────────────


def derive_name_from_email(email: str) -> str:
    """`carlos.perez@corp.com` → "Carlos Perez". Puro."""
    local = email.split("@", 1)[0]
    parts = [p for p in re.split(r"[._\-+]+", local) if p]
    return " ".join(p.capitalize() for p in parts) or email


def _scim_display_name_sync(email: str) -> str | None:
    """Display name del usuario vía SCIM del workspace. Reusa la MISMA cadena
    de credenciales que Lakebase (PAT dev / OAuth M2M del SP en Apps). El SP
    puede no tener permiso para listar usuarios → None (fallback)."""
    from app.core.db.lakebase.credentials import workspace_client

    safe = email.replace('"', "").replace("\\", "")
    users = workspace_client().users.list(
        filter=f'userName eq "{safe}"', attributes="displayName,userName", count=1
    )
    for u in users:
        if getattr(u, "display_name", None):
            return u.display_name
    return None


async def resolve_display_name(email: str, username_hint: str | None) -> str:
    """SCIM → preferred_username (si no es un correo) → local-part del correo."""
    try:
        name = await asyncio.wait_for(
            asyncio.to_thread(_scim_display_name_sync, email), timeout=_SCIM_TIMEOUT_S
        )
        if name:
            return name
    except Exception:  # noqa: BLE001 — sin permisos/credenciales/timeout → fallback
        log.info("sso: SCIM lookup no disponible, derivando nombre", extra={"email": email})
    if username_hint and not is_email(username_hint):
        return username_hint
    return derive_name_from_email(email)
