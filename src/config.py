"""Configuración centralizada del sistema Data Modeler Agent.

Carga variables de entorno desde .env y expone la configuración
como un singleton accesible desde cualquier módulo.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Cargar variables de entorno
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


class Settings:
    """Configuración global del sistema."""

    # ─── Azure AI Foundry ──────────────────────────────────────
    FOUNDRY_PROJECT_ENDPOINT: str = os.getenv(
        "FOUNDRY_PROJECT_ENDPOINT",
        "https://aaifdatacraft10.services.ai.azure.com/api/projects/agents-lab",
    )
    FOUNDRY_MODEL: str = os.getenv("FOUNDRY_MODEL", "gpt-4o")
    FOUNDRY_API_KEY: str = os.getenv("FOUNDRY_API_KEY", "")

    # ─── Azure Service Principal ───────────────────────────────
    APP_AZURE_TENANT_ID: str = os.getenv("APP_AZURE_TENANT_ID", "")
    APP_AZURE_CLIENT_ID: str = os.getenv("APP_AZURE_CLIENT_ID", "")
    APP_AZURE_CLIENT_SECRET: str = os.getenv("APP_AZURE_CLIENT_SECRET", "")

    # ─── Azure OpenAI (alternativo) ────────────────────────────
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

    # ─── Guidelines (carga por sesión desde chat — sin archivo global) ─
    GUIDELINES_PATH: str = (os.getenv("GUIDELINES_PATH") or "").strip()

    # ─── Azure Cosmos DB for MongoDB ───────────────────────────
    # Misma cuenta que el frontend (db_modeler). El catálogo de columnas
    # del agente se persiste en la colección `column_catalog`.
    COSMOS_CONNECTION_STRING: str = os.getenv("COSMOS_CONNECTION_STRING", "")
    COSMOS_DATABASE: str = os.getenv("COSMOS_DATABASE", "db_modeler")

    # ─── Motor de BD por defecto ───────────────────────────────
    DEFAULT_DB_ENGINE: str = os.getenv("DEFAULT_DB_ENGINE", "databricks_sql")

    # ─── Motores soportados ────────────────────────────────────
    SUPPORTED_ENGINES: list[str] = [
        "databricks_sql",
        "cosmosdb",
        "sqlserver",
        "postgresql",
        "mysql",
    ]

    # ─── Ruta raíz del proyecto ────────────────────────────────
    PROJECT_ROOT: Path = _PROJECT_ROOT


settings = Settings()
