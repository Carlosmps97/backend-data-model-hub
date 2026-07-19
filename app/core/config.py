"""Configuración del backend de plataforma (backend-data-model-hub).

Solo expone la conexión a Cosmos DB y la ruta raíz del proyecto. Las
variables de Azure AI Foundry / OpenAI / embeddings / engines viven en el
servicio de agentes (`app-agents-modeler`).

MVP sin auth: no hay secretos de autenticación (la identidad/permisos vuelven al
final como matriz robusta).
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


class Settings:
    """Configuración global del backend de plataforma."""

    # ─── Backend de base de datos ──────────────────────────────
    # "lakebase" (Databricks Lakebase Postgres, doc 28) o "cosmos" (legacy /
    # rollback). El seam es app/core/db/client.py: los repositorios no saben
    # cuál hay debajo.
    DB_BACKEND: str = os.getenv("DB_BACKEND", "cosmos").strip().lower()

    # ─── Azure Cosmos DB for MongoDB (legacy / rollback) ───────
    # Misma cuenta que el servicio de agentes. Cada uno toca colecciones
    # distintas: el backend administra `users` / `projects` / `project_tables`
    # / `project_relationships` / `semantic_types` / `udps`; el agente
    # administra `column_catalog`.
    COSMOS_CONNECTION_STRING: str = os.getenv("COSMOS_CONNECTION_STRING", "")
    COSMOS_DATABASE: str = os.getenv("COSMOS_DATABASE", "db_modeler")

    # ─── Databricks Lakebase Postgres (doc 28) ─────────────────
    # El PAT autentica el SDK ante el workspace; el password real de Postgres
    # es un token OAuth de ~1h que se acuña por conexión nueva del pool
    # (lakebase/credentials.py). `PAT_DATABRICKS` se acepta como alias porque
    # así está hoy en el .env del owner.
    DATABRICKS_HOST: str = os.getenv(
        "DATABRICKS_HOST", "https://adb-3871428306507680.0.azuredatabricks.net"
    )
    DATABRICKS_TOKEN: str = os.getenv("DATABRICKS_TOKEN", "") or os.getenv(
        "PAT_DATABRICKS", ""
    )
    LAKEBASE_ENDPOINT: str = os.getenv(
        "LAKEBASE_ENDPOINT", "projects/dmh-proj/branches/production/endpoints/primary"
    )
    PGHOST: str = os.getenv(
        "PGHOST", "ep-orange-sunset-e1gjz1qx.database.eastus2.azuredatabricks.net"
    )
    PGPORT: int = int(os.getenv("PGPORT", "5432"))
    # Rol de Postgres = identidad Databricks que acuña el token. En Databricks
    # Apps NO se setea PGUSER: cae al DATABRICKS_CLIENT_ID inyectado (rol PG
    # del service principal de la app — requiere el alta one-time de doc 28 §11).
    PGUSER: str = (
        os.getenv("PGUSER", "")
        or os.getenv("DATABRICKS_CLIENT_ID", "")
        or "carlosmps97@hotmail.com"
    )
    PGDATABASE: str = os.getenv("PGDATABASE", "databricks_postgres")
    PGSSLMODE: str = os.getenv("PGSSLMODE", "require")
    # Schema de Postgres donde viven las "colecciones" (tablas id+doc jsonb).
    LAKEBASE_PGSCHEMA: str = os.getenv("LAKEBASE_PGSCHEMA", "dmh")

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
                "SECRET_KEY inseguro con REQUIRE_AUTH=true: definí SECRET_KEY "
                "(env/secreto) antes de desplegar — con el default público se "
                "pueden forjar tokens de sesión admin."
            )
        log.warning(
            "SECRET_KEY usa el default de desarrollo (INSEGURO). Definí SECRET_KEY "
            "y REQUIRE_AUTH=true antes de cualquier despliegue."
        )
