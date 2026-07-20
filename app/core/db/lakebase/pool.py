"""Pool asyncpg hacia Lakebase con password rotativo y wake del compute.

- `password` es un callable async: cada conexión física nueva recibe un token
  fresco (credentials.fresh_token, cacheado ~50 min).
- El compute tiene scale-to-zero (5 min): la primera conexión tras idle puede
  tardar unos segundos en despertar — timeout generoso + retry.
- `statement_cache_size=256`: con cache=0 asyncpg paga DOS round-trips por
  query (Parse/Describe y recién después Bind/Execute) — medido: 399→~200 ms
  por operación desde Lima. El endpoint `primary` es DIRECTO (sesión dedicada,
  no pooler transaccional), así que los prepared statements son seguros.
- SIN codec jsonb custom: el adaptador manda JSON pre-serializado (`$n::jsonb`)
  y decodifica explícitamente al leer — un codec automático re-encodearía el
  texto y guardaría strings JSON dentro del jsonb (bug visto en la sonda F2).
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from app.core.config import settings
from app.core.db.lakebase.credentials import fresh_token, invalidate_token, pg_host

log = logging.getLogger(__name__)


async def _password() -> str:
    # El SDK es sync (HTTP): fuera del event loop.
    return await asyncio.to_thread(fresh_token)


async def create_pool() -> asyncpg.Pool:
    """Crea el pool (lazy: conecta al primer uso) y valida con un ping."""
    if not settings.PGUSER:
        raise RuntimeError(
            "PGUSER no está seteado: en dev local es tu identidad Databricks "
            "(correo del workspace); en Databricks Apps se toma solo del "
            "DATABRICKS_CLIENT_ID inyectado."
        )
    # PGHOST explícito o auto-resuelto desde LAKEBASE_ENDPOINT (SDK sync →
    # fuera del event loop).
    host = await asyncio.to_thread(pg_host)
    pool = await asyncpg.create_pool(
        host=host,
        port=settings.PGPORT,
        user=settings.PGUSER,
        database=settings.PGDATABASE,
        password=_password,
        ssl=settings.PGSSLMODE or "require",
        min_size=0,
        max_size=10,
        max_inactive_connection_lifetime=300.0,
        timeout=75.0,  # cubre el wake del compute suspendido
        statement_cache_size=256,
        server_settings={"application_name": "dmh-backend"},
    )
    # Ping con retry: despierta el compute y descarta un token cacheado inválido.
    last: Exception | None = None
    for attempt in range(3):
        try:
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return pool
        except asyncpg.exceptions.InvalidPasswordError as exc:
            last = exc
            invalidate_token()
            log.warning("lakebase: password inválido, re-acuñando token (intento %d)", attempt + 1)
        except Exception as exc:  # noqa: BLE001 — compute despertando / red
            last = exc
            log.warning("lakebase: ping falló (intento %d): %s", attempt + 1, exc)
            await asyncio.sleep(2.0 * (attempt + 1))
    await pool.close()
    raise RuntimeError(f"No se pudo conectar a Lakebase: {last}") from last
