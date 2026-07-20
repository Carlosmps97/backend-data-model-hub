"""Rate limiting (slowapi) — mitigación de fuerza bruta / credential stuffing.

Se aplica por-ruta con el decorator `@limiter.limit(...)` (p.ej. en /api/auth/login),
sin límite GLOBAL para no frenar el uso legítimo (canvas y reporting disparan
muchos requests). Key = IP del cliente.

ESTADO EN MEMORIA (por proceso): suficiente para una sola instancia. En producción
multi-réplica (Databricks Apps con varias instancias) migrar a un backend Redis
(`Limiter(storage_uri="redis://...")` / fastapi-limiter), si no cada réplica cuenta
por separado y el límite efectivo se multiplica por el nº de réplicas.
"""
from __future__ import annotations

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

# Activo en postura de producción (REQUIRE_AUTH) o con RATE_LIMIT_ENABLED=true.
# En dev/tests queda OFF por defecto para no frenar el harness (muchos logins
# desde localhost) ni el uso local.
_ENABLED = settings.REQUIRE_AUTH or os.getenv("RATE_LIMIT_ENABLED", "").lower() in ("1", "true", "yes")

limiter = Limiter(key_func=get_remote_address, headers_enabled=True, enabled=_ENABLED)
