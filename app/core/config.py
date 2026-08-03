"""Configuración del backend de plataforma (backend-data-model-hub).

ÚNICA superficie de configuración por entorno (homologada 2026-07-19):
- Dev local  → `.env` (gitignored; plantilla en `.env.example`).
- Databricks Apps → bloque `config.env` del bundle (`databricks.yml`,
  sección `variables:` = parámetros por entorno).
- GitHub Actions → solo `DATABRICKS_HOST` (variable) + `DATABRICKS_TOKEN`
  (secret) para el CLI del bundle; no alimentan este Settings.

Regla: NINGÚN valor específico de un workspace va hardcodeado aquí como
default — los defaults son constantes de producto (puertos, nombres de BD
del producto, flags). Lo específico del entorno entra por env.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Raíz del repo: app/core/config.py → parents[2] == backend-data-model-hub/
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

# Clave de firma de tokens SOLO para desarrollo local. Si en producción
# (`REQUIRE_AUTH=true`) el `SECRET_KEY` sigue siendo este valor, la app NO
# arranca (ver `assert_secure_config`): con esta clave pública cualquiera
# forjaría un JWT admin.
INSECURE_DEFAULT_SECRET_KEY = "dev-only-insecure-change-me-in-prod"


def _csv(name: str) -> list[str]:
    """Lista desde una env separada por comas (vacía → [])."""
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


class Settings:
    """Configuración global del backend de plataforma."""

    # ─── Databricks Lakebase Postgres (doc 28) ─────────────────
    # BD única del backend (adaptador JSONB en app/core/db/lakebase/). El seam
    # es app/core/db/client.py: los repositorios no ven Postgres directamente.
    # Identidad ante el workspace (para acuñar el token OAuth de BD ~1h que
    # Postgres acepta como password — lakebase/credentials.py):
    #   · Dev local: DATABRICKS_HOST + DATABRICKS_TOKEN (PAT) del .env.
    #   · Databricks Apps: NO se setean — el runtime inyecta DATABRICKS_HOST y
    #     DATABRICKS_CLIENT_ID/SECRET del service principal (OAuth M2M).
    DATABRICKS_HOST: str = os.getenv("DATABRICKS_HOST", "")
    DATABRICKS_TOKEN: str = os.getenv("DATABRICKS_TOKEN", "")
    # Ruta LÓGICA del endpoint (projects/<proyecto>/branches/<branch>/
    # endpoints/<endpoint>). Es la MISMA en cualquier workspace que respete la
    # convención de nombres; obligatoria en operación normal (salvo el atajo
    # PGHOST+PGPASSWORD de más abajo para scripts sin SDK).
    LAKEBASE_ENDPOINT: str = os.getenv("LAKEBASE_ENDPOINT", "")
    # Host físico del endpoint (ep-…). OPCIONAL: si está vacío se resuelve
    # solo vía SDK a partir de LAKEBASE_ENDPOINT (credentials.pg_host) — así
    # ningún entorno necesita hardcodearlo. Setear solo para forzar/depurar.
    PGHOST: str = os.getenv("PGHOST", "")
    PGPORT: int = int(os.getenv("PGPORT", "5432"))
    # Rol de Postgres = identidad Databricks que acuña el token. En Databricks
    # Apps NO se setea PGUSER: cae al DATABRICKS_CLIENT_ID inyectado (rol PG
    # del service principal de la app — requiere el alta one-time de doc 28 §11).
    PGUSER: str = os.getenv("PGUSER", "") or os.getenv("DATABRICKS_CLIENT_ID", "")
    PGDATABASE: str = os.getenv("PGDATABASE", "databricks_postgres")
    PGSSLMODE: str = os.getenv("PGSSLMODE", "require")
    # Password de Postgres FIJO (el "OAuth token" que la consola de Lakebase
    # deja copiar; dura ~1 h). OPCIONAL y solo para correr scripts desde una
    # máquina donde no se puede autenticar el SDK: si está seteado, el pool lo
    # usa tal cual y NO acuña token (tampoco hace falta LAKEBASE_ENDPOINT si
    # además se setea PGHOST). En la app desplegada nunca se usa.
    PGPASSWORD: str = os.getenv("PGPASSWORD", "")
    # Sabor de negociacion TLS de Postgres: vacio = auto (prueba el clasico y,
    # si el servidor resetea, el DIRECTO). "true" fuerza TLS directo (front-ends
    # que enrutan por SNI/ALPN, p.ej. Databricks "service direct"); "false"
    # fuerza el clasico. Ver app/core/db/lakebase/pool.py.
    PGDIRECTTLS: str = os.getenv("PGDIRECTTLS", "")
    # Schema de Postgres donde viven las "colecciones" (tablas id+doc jsonb).
    LAKEBASE_PGSCHEMA: str = os.getenv("LAKEBASE_PGSCHEMA", "dmh")

    # ─── HTTP: CORS y hosts permitidos ─────────────────────────
    # Regex de orígenes permitidos (adicional a la lista). Permite que el
    # MISMO bundle sirva en cualquier workspace: el front de Databricks Apps
    # siempre matchea `https://frnt-data-model-hub-….databricksapps.com`.
    CORS_ORIGIN_REGEX: str = os.getenv("CORS_ORIGIN_REGEX", "")
    # Orígenes exactos permitidos, separados por coma. El default (front de
    # dev en localhost) SOLO aplica si tampoco hay regex — en producción la
    # regex sola no abre localhost.
    CORS_ORIGINS: list[str] = _csv("CORS_ORIGINS") or (
        []
        if os.getenv("CORS_ORIGIN_REGEX")
        else ["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    # TrustedHostMiddleware (vacío → deshabilitado).
    ALLOWED_HOSTS: list[str] = _csv("ALLOWED_HOSTS")

    # ─── Logging ───────────────────────────────────────────────
    # LOG_FORMAT: "pretty" (dev) | "json" (producción). LOG_LEVEL: DEBUG…CRITICAL.
    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "pretty").strip().lower()
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").strip().upper()

    # ─── Identidad / Auth seam ─────────────────────────────────
    # Auth PROPIA (decisión 2026-07-04): login usuario/contraseña en TODOS los
    # entornos. La identidad sale del token de sesión firmado (no de headers).
    # `AUTH_MODE` se conserva por compat, pero el carril real es el token.
    # "databricks" seguiría permitiendo derivar identidad de headers OBO si se
    # reactivara; hoy no se usa para autenticar.
    AUTH_MODE: str = os.getenv("AUTH_MODE", "local")
    LOCAL_DEV_USER: str = os.getenv("LOCAL_DEV_USER", "dev@local")
    LOCAL_DEV_USERNAME: str = os.getenv("LOCAL_DEV_USERNAME", "")
    LOCAL_DEV_DISPLAY_NAME: str = os.getenv("LOCAL_DEV_DISPLAY_NAME", "")

    # ─── Sesión / firma del token ──────────────────────────────
    # Clave HMAC para firmar el token de sesión (JWT HS256). En producción
    # DEBE venir de env (`SECRET_KEY`); el default es solo para desarrollo local.
    # `assert_secure_config()` (app/main.py) IMPIDE arrancar con este default si
    # `REQUIRE_AUTH` está activo — sin eso, un atacante forjaría tokens admin.
    SECRET_KEY: str = os.getenv("SECRET_KEY", INSECURE_DEFAULT_SECRET_KEY)
    # Vida del token de acceso (minutos); default 12h (jornada de trabajo).
    ACCESS_TOKEN_TTL_MIN: int = int(os.getenv("ACCESS_TOKEN_TTL_MIN", "720"))
    # Si es True, una request SIN token válido es 401 (producción: login
    # obligatorio). En local/tests (default False) se permite el fallback del
    # seam de identidad (X-Dev-User / usuario fake) para no exigir login al
    # desarrollar ni en los tests que no montan DB. Poner `REQUIRE_AUTH=true`
    # en el deploy.
    REQUIRE_AUTH: bool = os.getenv("REQUIRE_AUTH", "").lower() in ("1", "true", "yes")

    # ─── Ruta raíz del proyecto ────────────────────────────────
    PROJECT_ROOT: Path = _PROJECT_ROOT


settings = Settings()


def assert_secure_config() -> None:
    """Falla-cerrado: en postura de producción (`REQUIRE_AUTH=true`) NO se
    permite arrancar con el `SECRET_KEY` de desarrollo (público) — con esa clave
    cualquiera firmaría un token de sesión admin. Se llama en `create_app()`.

    Siempre loguea una advertencia fuerte si se está usando el default (útil en
    dev para no olvidarlo)."""
    import logging

    log = logging.getLogger("app.config")
    using_default = settings.SECRET_KEY == INSECURE_DEFAULT_SECRET_KEY
    if using_default:
        if settings.REQUIRE_AUTH:
            raise RuntimeError(
                "SECRET_KEY inseguro con REQUIRE_AUTH=true: define SECRET_KEY "
                "(env/secreto) antes de desplegar — con el default público se "
                "pueden forjar tokens de sesión admin."
            )
        log.warning(
            "SECRET_KEY usa el default de desarrollo (INSEGURO). Define SECRET_KEY "
            "y REQUIRE_AUTH=true antes de cualquier despliegue."
        )
