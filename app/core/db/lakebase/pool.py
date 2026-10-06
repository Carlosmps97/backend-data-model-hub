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
import ssl
from contextlib import suppress

import asyncpg

from app.core.config import settings
from app.core.db.lakebase.credentials import fresh_token, invalidate_token, pg_host

log = logging.getLogger(__name__)

# Red caída o lenta, o servidor que corta la sesión (reinicio, apagado,
# cancelación): la conexión no sirve, pero otra sí puede.
NETWORK_ERRORS = (
    TimeoutError,
    OSError,
    asyncpg.exceptions.InterfaceError,
    asyncpg.exceptions.PostgresConnectionError,
    asyncpg.exceptions.OperatorInterventionError,
)

# El `DataError` del CLIENTE de asyncpg (un parámetro que no se puede codificar)
# hereda de InterfaceError, pero no es la red: nunca se reintenta.
CLIENT_DATA_ERROR = asyncpg.exceptions._base.DataError


def is_network_error(exc: BaseException) -> bool:
    return isinstance(exc, NETWORK_ERRORS) and not isinstance(exc, CLIENT_DATA_ERROR)


# Doc 110: prueba de subida de cada conexión nueva de los scripts. Desde Lima,
# una conexión en ruta buena sube 256 KB en ~0.3 s; en la ruta con pérdida
# (50-70 KB/s) tarda 4-5 s. Dentro de Databricks, milisegundos.
_PROBE_BYTES = 256 * 1024
_PROBE_TIMEOUT_S = 2.0
_PROBE_TRIES = 8
PROBE_STATS = {"descartadas": 0}


async def probing_connect(*args, **kwargs) -> asyncpg.Connection:
    """Gancho `connect=` del pool de los scripts (doc 110): abre la conexión y
    la prueba subiendo 256 KB; si no llega en 2 s (o se cae), la descarta y abre
    otra. El último intento se acepta sin probar: una red pareja y lenta no
    frena la carga, sólo queda avisada."""
    for attempt in range(1, _PROBE_TRIES + 1):
        conn = await asyncpg.connect(*args, **kwargs)
        if attempt == _PROBE_TRIES:
            log.warning("lakebase: %d conexiones lentas seguidas; se sigue con la última",
                        _PROBE_TRIES - 1)
            return conn
        try:
            await asyncio.wait_for(conn.fetchval("SELECT length($1::text)", "x" * _PROBE_BYTES),
                                   _PROBE_TIMEOUT_S)
            return conn
        except NETWORK_ERRORS as exc:
            PROBE_STATS["descartadas"] += 1
            log.warning("lakebase: conexión lenta descartada (%s; intento %d/%d)",
                        type(exc).__name__, attempt, _PROBE_TRIES)
            conn.terminate()
    raise AssertionError("inalcanzable")


async def _password() -> str:
    # Escape hatch para scripts: password pegado a mano (el "OAuth token" que
    # la consola de Lakebase deja copiar). Sin SDK de por medio.
    if settings.PGPASSWORD:
        return settings.PGPASSWORD
    # El SDK es sync (HTTP): fuera del event loop.
    return await asyncio.to_thread(fresh_token)


def _ssl_arg(direct_tls: bool):
    """`ssl=` para asyncpg. En negociación clásica basta el string del sslmode.
    En TLS DIRECTO hace falta un contexto explícito con ALPN `postgresql`: los
    front-ends que enrutan por SNI/ALPN (Databricks "service direct") lo piden,
    y con `require` no se verifica el certificado (misma semántica de siempre).
    """
    mode = (settings.PGSSLMODE or "require").lower()
    if not direct_tls:
        return mode
    ctx = ssl.create_default_context()
    if mode not in ("verify-ca", "verify-full"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with suppress(NotImplementedError):
        ctx.set_alpn_protocols(["postgresql"])
    return ctx


async def _open_pool(host: str, direct_tls: bool, probe: bool = False) -> asyncpg.Pool:
    """Crea el pool en UN modo de TLS y lo valida con un ping (con retry para
    el wake del compute y para re-acuñar un token cacheado inválido). Con
    `probe` (sólo scripts, doc 110), cada conexión nueva se prueba."""
    kwargs = {"direct_tls": True} if direct_tls else {}
    pool = await asyncpg.create_pool(
        connect=probing_connect if probe else None,
        host=host,
        port=settings.PGPORT,
        user=settings.PGUSER,
        database=settings.PGDATABASE,
        password=_password,
        ssl=_ssl_arg(direct_tls),
        min_size=0,
        max_size=15,  # × nº de workers uvicorn = conexiones reales (ver app.yaml)
        max_inactive_connection_lifetime=300.0,
        timeout=75.0,  # cubre el wake del compute suspendido
        statement_cache_size=256,
        server_settings={"application_name": "dmh-backend"},
        **kwargs,
    )
    last: Exception | None = None
    for attempt in range(3):
        try:
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return pool
        except asyncpg.exceptions.InvalidPasswordError as exc:
            last = exc
            if settings.PGPASSWORD:
                await pool.close()
                raise RuntimeError(
                    "Lakebase rechazó el PGPASSWORD del .env: el token de la "
                    "consola dura ~1 h. Copia uno nuevo (Connect > Copy OAuth "
                    "token) y vuelve a correr."
                ) from exc
            invalidate_token()
            log.warning("lakebase: password inválido, re-acuñando token (intento %d)", attempt + 1)
        except (ConnectionResetError, ConnectionRefusedError):
            # El servidor corta al primer byte: no tiene sentido reintentar en
            # este modo — que decida create_pool si prueba el otro.
            await pool.close()
            raise
        except Exception as exc:  # noqa: BLE001 — compute despertando / red
            last = exc
            log.warning("lakebase: ping falló (intento %d): %s", attempt + 1, exc)
            await asyncio.sleep(2.0 * (attempt + 1))
    await pool.close()
    assert last is not None
    raise last


async def create_pool(probe: bool = False) -> asyncpg.Pool:
    """Crea el pool probando el modo de TLS que corresponda. `probe` (doc 110):
    conexiones probadas, sólo para los scripts (`app/core/db/sync.py`).

    Postgres negocia TLS en dos sabores y no todos los endpoints aceptan los
    dos: el CLÁSICO (paquete `SSLRequest` en texto plano y recién ahí TLS) y el
    DIRECTO (TLS desde el primer byte, PG 17). Un front-end que enruta por
    SNI/ALPN no entiende el primero y **corta la conexión** (WinError 10054 /
    ConnectionResetError) apenas llega ese paquete. `PGDIRECTTLS` fuerza uno de
    los dos; sin ella se prueba clásico y, si el servidor resetea, directo.
    """
    if not settings.PGUSER:
        raise RuntimeError(
            "PGUSER no está seteado: en dev local es tu identidad Databricks "
            "(correo del workspace); en Databricks Apps se toma solo del "
            "DATABRICKS_CLIENT_ID inyectado."
        )
    # PGHOST explícito o auto-resuelto desde LAKEBASE_ENDPOINT (SDK sync →
    # fuera del event loop).
    host = await asyncio.to_thread(pg_host)

    forced = (settings.PGDIRECTTLS or "").strip().lower()
    if forced in ("1", "true", "yes", "si", "sí"):
        modes = [True]
    elif forced in ("0", "false", "no"):
        modes = [False]
    else:
        modes = [False, True]

    last: Exception | None = None
    for direct_tls in modes:
        try:
            pool = await _open_pool(host, direct_tls, probe)
            if direct_tls:
                log.info("lakebase: conectado con TLS directo (setea PGDIRECTTLS=true "
                         "para saltarte el intento clásico)")
            return pool
        except (ConnectionResetError, ConnectionRefusedError) as exc:
            last = exc
            log.warning("lakebase: el servidor cortó la conexión con TLS %s (%s)",
                        "directo" if direct_tls else "clásico", exc)
        except Exception as exc:  # noqa: BLE001
            last = exc
            break  # error real (password, permisos, DNS): no vale probar el otro modo
    raise RuntimeError(f"No se pudo conectar a Lakebase: {last}") from last
