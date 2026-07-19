"""Token OAuth de BD para Lakebase (vida ~60 min) con caché de ~50.

Dos modos de autenticar el SDK ante el workspace para acuñar el token corto
que Postgres acepta como password:
- Dev local: PAT (`DATABRICKS_TOKEN` / alias `PAT_DATABRICKS`) del .env.
- Databricks Apps: SIN PAT — el runtime inyecta `DATABRICKS_HOST` y
  `DATABRICKS_CLIENT_ID`/`DATABRICKS_CLIENT_SECRET` del service principal de
  la app, y `WorkspaceClient()` sin args los toma solo (OAuth M2M).

Solo las conexiones NUEVAS del pool necesitan token fresco — las vivas siguen
operando aunque el token con el que abrieron ya venció.
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


def fresh_token() -> str:
    """Token de BD vigente (cacheado). Thread-safe; llamada bloqueante (~1 s
    cuando acuña — el pool la invoca vía `asyncio.to_thread`)."""
    global _cached
    with _lock:
        if _cached and (time.monotonic() - _cached[1]) < _TOKEN_TTL_S:
            return _cached[0]
        from databricks.sdk import WorkspaceClient

        if settings.DATABRICKS_TOKEN:
            w = WorkspaceClient(
                host=settings.DATABRICKS_HOST, token=settings.DATABRICKS_TOKEN
            )
        elif os.getenv("DATABRICKS_CLIENT_ID"):
            w = WorkspaceClient()  # Apps: OAuth M2M ambiente del SP
        else:
            raise RuntimeError(
                "Sin credenciales para Lakebase: setea DATABRICKS_TOKEN (o "
                "PAT_DATABRICKS) en dev local, o corre dentro de Databricks "
                "Apps (service principal inyectado)."
            )
        cred = w.postgres.generate_database_credential(
            endpoint=settings.LAKEBASE_ENDPOINT
        )
        _cached = (cred.token, time.monotonic())
        log.info("lakebase: token de BD acuñado", extra={"endpoint": settings.LAKEBASE_ENDPOINT})
        return cred.token


def invalidate_token() -> None:
    """Descarta el token cacheado (p. ej. tras un fallo de auth)."""
    global _cached
    with _lock:
        _cached = None
