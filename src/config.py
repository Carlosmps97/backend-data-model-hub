"""Configuración del backend de plataforma (backend-data-model-hub).

Solo expone la conexión a Cosmos DB y la ruta raíz del proyecto. Las
variables de Azure AI Foundry / OpenAI / embeddings / engines viven
ahora en el servicio de agentes (`app-agents-modeler`).

La autenticación lee `AUTH_SECRET` directamente desde `os.getenv` en
`src/api/auth.py` — no se expone acá para evitar leakage accidental.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Cargar variables de entorno
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


class Settings:
    """Configuración global del backend de plataforma."""

    # ─── Azure Cosmos DB for MongoDB ───────────────────────────
    # Misma cuenta que el servicio de agentes. Cada uno toca colecciones
    # distintas: el backend administra `users` / `projects` / `models` /
    # `model_tables` / `model_relationships` / `model_views`; el agente
    # administra `column_catalog`.
    COSMOS_CONNECTION_STRING: str = os.getenv("COSMOS_CONNECTION_STRING", "")
    COSMOS_DATABASE: str = os.getenv("COSMOS_DATABASE", "db_modeler")

    # ─── Ruta raíz del proyecto ────────────────────────────────
    PROJECT_ROOT: Path = _PROJECT_ROOT


settings = Settings()
