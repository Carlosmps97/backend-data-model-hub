"""Rate limiting (slowapi) — mitigación de fuerza bruta / credential stuffing.

Se aplica por-ruta con el decorator `@limiter.limit(...)` (p.ej. en /api/auth/login),
sin límite GLOBAL para no frenar el uso legítimo (canvas y reporting disparan
muchos requests). Key = IP del cliente.

ESTADO EN MEMORIA (por PROCESO): `app.yaml` corre `uvicorn --workers 2`, así que
cada worker cuenta por separado y el límite efectivo por IP es hasta el doble (y
se multiplica otra vez por cada réplica). La defensa principal contra fuerza
bruta es el lockout por cuenta, que vive en la BD (compartido por todos los
procesos). Un límite exacto necesitaría un storage compartido
(`Limiter(storage_uri="redis://...")`).
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


def client_ip(request) -> str:
    """IP real del cliente para la key del limiter.

    En Databricks Apps el backend NUNCA ve al navegador: recibe del proxy de
    Databricks (y desde doc 36 también del server del front). Con la key por
    `request.client` TODOS los usuarios comparten la IP del último salto y el
    límite de login (5/min) se vuelve GLOBAL — con usuarios en paralelo, los
    logins legítimos rebotarían en 429. La IP real viaja en `X-Forwarded-For`
    (el proxy de Databricks la pone; server.mjs la preserva): se toma la
    PRIMERA de la cadena. Sin header (dev local directo) cae al peer.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return get_remote_address(request)


limiter = Limiter(key_func=client_ip, headers_enabled=True, enabled=_ENABLED)
