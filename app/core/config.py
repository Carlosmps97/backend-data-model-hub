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


class Settings:
    """Configuración global del backend de plataforma."""

    # ─── Azure Cosmos DB for MongoDB ───────────────────────────
    # Misma cuenta que el servicio de agentes. Cada uno toca colecciones
    # distintas: el backend administra `users` / `projects` / `project_tables`
    # / `project_relationships` / `semantic_types` / `udps`; el agente
    # administra `column_catalog`.
    COSMOS_CONNECTION_STRING: str = os.getenv("COSMOS_CONNECTION_STRING", "")
    COSMOS_DATABASE: str = os.getenv("COSMOS_DATABASE", "db_modeler")

    # ─── Identidad / Auth seam ─────────────────────────────────
    # "local"  → usuario fake configurable (desarrollo sin Databricks).
    # "databricks" → identidad reenviada por el proxy SSO (headers OBO).
    AUTH_MODE: str = os.getenv("AUTH_MODE", "local")
    LOCAL_DEV_USER: str = os.getenv("LOCAL_DEV_USER", "dev@local")
    LOCAL_DEV_USERNAME: str = os.getenv("LOCAL_DEV_USERNAME", "")
    LOCAL_DEV_DISPLAY_NAME: str = os.getenv("LOCAL_DEV_DISPLAY_NAME", "")

    # ─── Ruta raíz del proyecto ────────────────────────────────
    PROJECT_ROOT: Path = _PROJECT_ROOT


settings = Settings()
