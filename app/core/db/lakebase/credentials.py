"""Credenciales y descubrimiento de Lakebase vía el SDK de Databricks.

Dos cosas salen de aquí (ambas cacheadas y thread-safe):
- `fresh_token()`: token OAuth de BD (vida ~60 min, caché ~50) que Postgres
  acepta como password. Solo las conexiones NUEVAS del pool necesitan token
  fresco — las vivas siguen operando aunque el suyo ya venció.
- `pg_host()`: host físico del endpoint (`ep-…`). Si `PGHOST` no está seteado,
  se resuelve solo desde la ruta LÓGICA `LAKEBASE_ENDPOINT` — así ningún
  entorno hardcodea el host (la ruta lógica es la misma en cualquier
  workspace que respete la convención de nombres, doc 28 §11).

Autenticación del SDK ante el workspace:
- Dev local: PAT (`DATABRICKS_TOKEN`) + `DATABRICKS_HOST` del .env.
- Databricks Apps: SIN PAT — el runtime inyecta `DATABRICKS_HOST` y
  `DATABRICKS_CLIENT_ID`/`DATABRICKS_CLIENT_SECRET` del service principal de
  la app, y `WorkspaceClient()` sin args los toma solo (OAuth M2M).
"""

from __future__ import annotations

import logging
import os
import threading
import time

from app.core.config import settings

log = logging.getLogger(__name__)

# 50 min < 60 de vida real: margen para que ninguna conexión se abra con un
# token al borde de vencer.
_TOKEN_TTL_S = 50 * 60

_lock = threading.Lock()
_cached: tuple[str, float] | None = None  # (token, monotonic de acuñado)
_cached_host: str | None = None  # host del endpoint (estable; caché de proceso)


def _require_endpoint() -> str:
    if not settings.LAKEBASE_ENDPOINT:
        raise RuntimeError(
            "LAKEBASE_ENDPOINT no está seteado (projects/<proyecto>/branches/"
            "<branch>/endpoints/<endpoint>): defínelo en .env (dev) o en las "
            "variables del bundle (Databricks Apps)."
        )
    return settings.LAKEBASE_ENDPOINT


def _workspace_client():
    from databricks.sdk import WorkspaceClient

    if settings.DATABRICKS_TOKEN:
        if not settings.DATABRICKS_HOST:
            raise RuntimeError(
                "DATABRICKS_HOST no está seteado: con DATABRICKS_TOKEN (PAT) "
                "también se necesita la URL del workspace."
            )
        return WorkspaceClient(
            host=settings.DATABRICKS_HOST, token=settings.DATABRICKS_TOKEN
        )
    if os.getenv("DATABRICKS_CLIENT_ID"):
        return WorkspaceClient()  # Apps: OAuth M2M ambiente del SP
    # Dev SIN PAT (workspaces corporativos que no dejan crear tokens): se deja
    # que el SDK resuelva por su cadena unificada — OAuth U2M de
    # `databricks auth login`, perfil de `~/.databrickscfg`, Azure CLI…
    # NO se le pasa `host=`: con host explícito el SDK deja de buscar el perfil
    # en el archivo de config (verificado 2026-07-27). El host sale del perfil
    # o de la variable DATABRICKS_HOST, que el SDK lee solo.
    # La identidad resultante es TU usuario → `PGUSER` debe ser tu correo del
    # workspace, y ese correo necesita rol en el proyecto Lakebase.
    profile = os.getenv("DATABRICKS_CONFIG_PROFILE")
    try:
        return WorkspaceClient(profile=profile) if profile else WorkspaceClient()
    except Exception as exc:  # noqa: BLE001 — el SDK levanta su propio error
        raise RuntimeError(
            "Sin credenciales para Lakebase. Opciones: (a) DATABRICKS_TOKEN + "
            "DATABRICKS_HOST en el .env; (b) `databricks auth login --host "
            "<workspace>` y dejar DATABRICKS_TOKEN vacía (si el perfil no es "
            "el DEFAULT, indícalo con DATABRICKS_CONFIG_PROFILE); (c) correr "
            "dentro de Databricks Apps (service principal inyectado)."
        ) from exc


def workspace_client():
    """Cliente del workspace con la MISMA cadena de credenciales que Lakebase
    (PAT dev / OAuth M2M del SP en Apps / perfil del SDK). Lo usa también el
    lookup SCIM del login SSO (app/features/auth/sso.py)."""
    return _workspace_client()


def fresh_token() -> str:
    """Token de BD vigente (cacheado). Thread-safe; llamada bloqueante (~1 s
    cuando acuña — el pool la invoca vía `asyncio.to_thread`)."""
    global _cached
    with _lock:
        if _cached and (time.monotonic() - _cached[1]) < _TOKEN_TTL_S:
            return _cached[0]
        cred = _workspace_client().postgres.generate_database_credential(
            endpoint=_require_endpoint()
        )
        _cached = (cred.token, time.monotonic())
        log.info(
            "lakebase: token de BD acuñado",
            extra={"endpoint": settings.LAKEBASE_ENDPOINT},
        )
        return cred.token


def pg_host() -> str:
    """Host físico del endpoint. `PGHOST` explícito manda; si falta, se
    resuelve UNA vez por proceso vía `get_endpoint` sobre la ruta lógica."""
    global _cached_host
    if settings.PGHOST:
        return settings.PGHOST
    with _lock:
        if _cached_host:
            return _cached_host
        ep = _workspace_client().postgres.get_endpoint(name=_require_endpoint())
        host = ep.status.hosts.host if ep.status and ep.status.hosts else None
        if not host:
            raise RuntimeError(
                f"No se pudo resolver el host del endpoint "
                f"{settings.LAKEBASE_ENDPOINT!r}; setea PGHOST para forzarlo."
            )
        _cached_host = host
        log.info("lakebase: host del endpoint resuelto", extra={"pghost": host})
        return host


def invalidate_token() -> None:
    """Descarta el token cacheado (p. ej. tras un fallo de auth)."""
    global _cached
    with _lock:
        _cached = None
