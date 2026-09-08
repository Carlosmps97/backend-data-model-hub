"""Composition root del backend de plataforma (backend-data-model-hub).

Construye la app FastAPI y monta los routers de cada feature. Responsabilidades
acotadas:
- Configurar logging.
- Lifespan: abrir/cerrar la conexión a la base de datos (Databricks Lakebase).
- Middleware: logueo estructurado de cada request.
- CORS para el frontend Next.js (origen distinto en desarrollo local).
- Montar los routers de cada feature: `health`, `identity`, `domains`,
  `dictionary`, `catalog`, `changesets`, `projects` (+ subject areas),
  `folders`, `relationships`, `views`, `summary` (counts del Home), `reporting`
  (agregación tabular de metadata, solo lectura), `settings` (naming_config:
  separador/case por scope) y `bulk_upload` (carga masiva desde Excel dentro
  de un draft, doc 55).

Identidad por seam conmutable (`app/core/identity`, modo local/databricks). El
modelo de permisos por rol se reintroducirá al final como una matriz robusta.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.core.db import client as db_client
from app.core.logging import configure_logging, get_logger
from app.core.ratelimit import limiter
from app.features.admin.router import router as admin_router
from app.features.auth.router import router as auth_router
from app.features.bulk_upload.router import router as bulk_upload_router
from app.features.catalog.router import projects_catalog_router, router as catalog_router
from app.features.data_standards.router import router as data_standards_router
from app.features.ddl_rules.router import router as ddl_rules_router
from app.features.glossary.router import router as glossary_router
from app.features.domains.router import router as domains_router
from app.features.udp.router import router as udp_router
from app.features.folders.router import router as folders_router
from app.features.health.router import router as health_router
from app.features.identity.router import router as identity_router
from app.features.changesets.asof import AsOfUnavailable
from app.features.changesets.router import (
    project_versions_router,
    requests_router,
    router as changesets_router,
    versions_router,
)
from app.features.projects.router import router as projects_router
from app.features.relationships.router import router as relationships_router
from app.features.reporting.router import router as reporting_router
from app.features.reporting.query.router import router as reporting_query_router
from app.features.schemas.router import router as schemas_router, router_by_id as schemas_by_id_router
from app.features.settings.router import router as settings_router
from app.features.summary.router import router as summary_router
from app.features.views.router import router as views_router

configure_logging()
log = get_logger("app.main")


# ─── Lifespan ───────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa los singletons del proceso al arrancar."""
    retry_task: asyncio.Task | None = None
    try:
        await db_client.connect()
        app.state.db_connected = True
        log.info("db connected")
    except Exception as e:  # noqa: BLE001
        app.state.db_connected = False
        log.error("db connection failed", extra={"error": str(e)})

        # Auto-sanación: si la BD no estaba disponible AL ARRANCAR (p. ej. el
        # compute Lakebase dormido por scale-to-zero, o red transitoria), la
        # app quedaba `degraded` hasta un restart manual aunque la BD volviera
        # (visto en Apps 2026-07-20). Reintenta en background con backoff;
        # /api/health se recupera solo cuando conecta.
        async def _retry_connect() -> None:
            delay = 15.0
            while True:
                await asyncio.sleep(delay)
                try:
                    await db_client.connect()
                    app.state.db_connected = True
                    log.info("db connected after retry")
                    return
                except Exception as exc:  # noqa: BLE001
                    log.warning("db reconnect failed", extra={"error": str(exc)})
                    delay = min(delay * 2, 120.0)

        retry_task = asyncio.create_task(_retry_connect())

    log.info("api started")
    try:
        yield
    finally:
        log.info("api stopping")
        if retry_task is not None and not retry_task.done():
            retry_task.cancel()
        await db_client.disconnect()


# ─── Aplicación ─────────────────────────────────────────────────────────


async def _asof_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    """`AsOfUnavailable` → 404 (versión inexistente) / 409 (no publicada o
    irreconstruible), cuerpo `{detail}` como una HTTPException."""
    reason = getattr(exc, "reason", "")
    return JSONResponse(status_code=404 if reason == "not-found" else 409,
                        content={"detail": str(exc)})


def create_app() -> FastAPI:
    """Factory de la app FastAPI (CORS + middleware + routers de features)."""
    # Falla-cerrado: no arrancar en prod con el SECRET_KEY de desarrollo.
    from app.core.config import assert_secure_config
    assert_secure_config()

    app = FastAPI(
        title="Data Modeler Platform Backend",
        description=(
            "API REST de plataforma del Data Modeler. Maneja proyectos, modelos "
            "y su persistencia en Databricks Lakebase Postgres."
        ),
        version="1.0.0",
        lifespan=lifespan,
        # Postura de producción (REQUIRE_AUTH): ocultar la doc interactiva y el
        # schema OpenAPI reduce fingerprinting y superficie de ataque.
        docs_url=None if settings.REQUIRE_AUTH else "/docs",
        redoc_url=None if settings.REQUIRE_AUTH else "/redoc",
        openapi_url=None if settings.REQUIRE_AUTH else "/openapi.json",
    )

    # ─── Rate limiting (slowapi) ─────────────────────────────────────────
    # Se aplica por-ruta (el login lleva el límite estricto). RateLimitExceeded
    # → 429 con Retry-After. Estado en memoria: ver app/core/ratelimit.py.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    # Doc 70: el snapshot `asof:<versionId>` se resuelve en el repository de
    # changesets (una puerta para todos los lectores); si no se puede armar,
    # el error sube desde cualquier endpoint changeset-aware → mismo shape
    # `{detail}` de HTTPException que el front ya propaga al toast.
    app.add_exception_handler(AsOfUnavailable, _asof_unavailable_handler)

    # ─── Host allowlist (solo si ALLOWED_HOSTS está definido: producción) ──
    if settings.ALLOWED_HOSTS:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)

    # ─── CORS ───────────────────────────────────────────────────────────
    # Allowlist explícita (settings.CORS_ORIGINS; default = front de dev) y/o
    # regex (settings.CORS_ORIGIN_REGEX) para que el mismo bundle sirva en
    # cualquier workspace de Databricks Apps sin editar orígenes por entorno.
    # allow_credentials=True: NUESTRA auth sigue siendo token-first (Bearer,
    # sin cookies propias), pero en Databricks Apps el fetch del front lleva la
    # cookie de sesión del PROXY SSO de la app backend (`credentials:
    # 'include'` en http.ts). Con credenciales, el navegador EXIGE
    # `Access-Control-Allow-Credentials: true` en el preflight — con False el
    # POST de login ni se enviaba ("CORS error" con headers provisionales,
    # visto 2026-07-20 en Apps). Seguro porque los orígenes están acotados
    # (lista/regex, nunca wildcard) y la identidad sale SOLO del token.
    # Métodos y headers acotados en vez de wildcard (superficie mínima).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_origin_regex=settings.CORS_ORIGIN_REGEX or None,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        # X-Dev-User: header del seam de identidad de DESARROLLO. Permitirlo en
        # CORS no autentica nada (con REQUIRE_AUTH la identidad es SOLO el
        # token; el header se ignora), pero evita que un front que lo mande
        # (build viejo, dev apuntando a este backend) muera en el preflight
        # con 400 "Disallowed CORS headers" → "Failed to fetch".
        # X-Session-Token: el token de sesión propio viaja ahí en Databricks
        # Apps (el proxy SSO de la plataforma consume `Authorization`).
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Requested-With",
            "X-Dev-User",
            "X-Session-Token",
        ],
        max_age=600,
    )

    # ─── GZip ─────────────────────────────────────────────────────────────
    # Los payloads de metadata (claves repetidas, nombres) comprimen 5-10×;
    # a escala (diagramas de 100 tablas, diffs) es la diferencia entre cientos
    # de KB y varios MB por request.
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    # ─── Excepciones no controladas → envelope de error consistente ──────
    # Sin esto, un crash (BD caída, doc inválido) devolvía el 500 crudo de
    # Starlette (texto plano) y el frontend mostraba errores ilegibles. El
    # traceback ya lo loguea el middleware de requests.
    #
    # CORS a mano: el handler de `Exception` corre en ServerErrorMiddleware,
    # el middleware MÁS EXTERNO — la respuesta NO pasa por CORSMiddleware, y
    # sin Access-Control-Allow-Origin el browser bloquea la lectura y el
    # frontend ve "Failed to fetch" en vez del envelope.
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception(
            "unhandled exception",
            extra={"method": request.method, "path": request.url.path},
        )
        # Reponer los headers CORS a mano: esta respuesta se construye FUERA
        # del CORSMiddleware, y sin ellos el navegador convierte el 500 en un
        # opaco "Failed to fetch". (Fix 2026-07-20: referenciaba una variable
        # `cors_origins` inexistente → NameError en cada 500 de producción.)
        origin = request.headers.get("origin")
        allowed = bool(origin) and (
            origin in settings.CORS_ORIGINS
            or bool(
                settings.CORS_ORIGIN_REGEX
                and re.fullmatch(settings.CORS_ORIGIN_REGEX, origin)
            )
        )
        headers = (
            {
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Credentials": "true",
                "Vary": "Origin",
            }
            if allowed
            else {}
        )
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Internal server error. Check the logs using the X-Request-ID."},
            headers=headers,
        )

    # ─── Middleware: logueo de requests ─────────────────────────────────
    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next) -> Response:
        request_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        log.info(
            "request started",
            extra={"request_id": request_id, "method": request.method, "path": request.url.path},
        )
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            log.exception(
                "request crashed",
                extra={"request_id": request_id, "method": request.method, "path": request.url.path, "ms": elapsed_ms},
            )
            raise
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log.info(
            "request completed",
            extra={"request_id": request_id, "method": request.method, "path": request.url.path, "status": response.status_code, "ms": elapsed_ms},
        )
        response.headers["X-Request-ID"] = request_id
        # ─── Security headers (defensa en profundidad) ───────────────────
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # HSTS SOLO sobre HTTPS (no romper el dev local en http). Detrás de proxy
        # se detecta por X-Forwarded-Proto.
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    # ─── Routers de features ────────────────────────────────────────────
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(identity_router)
    app.include_router(domains_router)
    app.include_router(udp_router)
    app.include_router(glossary_router)
    app.include_router(data_standards_router)
    app.include_router(ddl_rules_router)
    app.include_router(catalog_router)
    app.include_router(projects_catalog_router)   # doc 75 D4
    app.include_router(changesets_router)
    # Carga masiva desde Excel (doc 55): jobs de validación/aplicación bajo
    # `/api/changesets/{cs}/uploads`. Va DESPUÉS del router de changesets
    # (sus rutas `/{cs_id}/...` son más generales) y antes de las estáticas
    # hermanas para que FastAPI resuelva el prefijo literal `/uploads`.
    app.include_router(bulk_upload_router)
    app.include_router(versions_router)
    app.include_router(project_versions_router)   # doc 75 D2
    app.include_router(requests_router)
    app.include_router(projects_router)
    app.include_router(folders_router)
    app.include_router(schemas_router)
    app.include_router(schemas_by_id_router)
    app.include_router(relationships_router)
    app.include_router(views_router)
    app.include_router(summary_router)
    app.include_router(reporting_router)
    app.include_router(reporting_query_router)
    app.include_router(settings_router)

    return app


app = create_app()


# ─── Ejecución directa (desarrollo) ─────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
