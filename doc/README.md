# Documentación del backend — Data Model Hub

Actualizado: 2026-07-31.

`backend-data-model-hub` es el servicio de plataforma del **Data Model Hub (DMH)**: una API REST en **FastAPI (Python)** que administra el modelo de datos canónico (tablas, columnas, relaciones, vistas), su estructura tipo Erwin (proyectos → folders → canvases), el versionado con flujo de aprobación (changesets), los Data Standards (glosario, dominios, UDP, reglas del DDL Export), el motor de consulta del reporting, y la identidad/RBAC/auditoría. La base de datos productiva es **Databricks Lakebase Postgres** (workspace corporativo), accedida a través de un adaptador propio con superficie tipo Motor/Mongo sobre documentos JSONB; Cosmos DB queda como fallback legacy (`DB_BACKEND=cosmos`).

## Orden de lectura

| # | Documento | Qué responde |
|---|-----------|--------------|
| 1 | [arquitectura.md](arquitectura.md) | La visión completa: capas (router → service → repository → db), stack y librerías con versiones exactas, scaffolding del árbol `app/`, ciclo de vida de un request, workflows de negocio y el adaptador Lakebase. **Punto de entrada.** |
| 2 | [esquema-datos.md](esquema-datos.md) | Las 21 colecciones campo por campo, el alcance del versionado, referencias entre colecciones y la foto del estado actual de la BD. |
| 3 | [api-contract.md](api-contract.md) | El contrato completo de la API (`/api/*`): convenciones, envelope, permisos y las 128 rutas con ejemplos de request/response. |
| 4 | [seguridad.md](seguridad.md) | Las tres capas de autenticación en producción (SSO de Databricks Apps, login propio JWT, service principal → Lakebase), rate limiting, CORS, RBAC, auditoría y endurecimiento. |
| 5 | [despliegue.md](despliegue.md) | Cómo y dónde corre: Databricks Apps vía bundle, deploy parametrizado por GitHub Variables, apps pre-creadas (bind), variables de entorno, arranque local y carga de data en un workspace nuevo. |
| 6 | [migracion-erwin.md](migracion-erwin.md) | El kit de migración Erwin XML → plataforma: gates de calidad, carga multi-archivo con reglas de merge, auto-arrange ELK, auditoría post-carga, seeds y marcador de versión base. |
| 7 | [testing.md](testing.md) | La estrategia de pruebas: 543 tests de la suite normal + 43 de la suite viva del adaptador, E2E contra backend real y comandos. |
| 8 | [consideraciones-y-limites.md](consideraciones-y-limites.md) | Límites conocidos y decisiones de escala: alcance del adaptador Lakebase, reglas del motor de reporting, escala probada (sintética y real) y mitigaciones pendientes. |

## Stack en una mirada

- **Lenguaje/runtime**: Python 3 + Uvicorn (ASGI).
- **Framework**: FastAPI `0.136.1` (+ Starlette `1.0.0`), Pydantic `2.13.4`.
- **Base de datos**: Databricks Lakebase Postgres vía `asyncpg 0.31.0` + `databricks-sdk 0.121.0` (token OAuth rotativo); adaptador propio en `app/core/db/lakebase/`. Cosmos (Motor `3.7.1` / PyMongo `4.17.0`) como fallback legacy.
- **Auth propia**: `bcrypt 5.0.0` + `pyjwt 2.12.1` (JWT HS256 en `X-Session-Token`).
- **SQL**: `sqlglot 30.12.0` — parser SQL → QuerySpec del reporting y motor de reglas del DDL Export.
- **Rate limiting**: `slowapi 0.1.10` (login, key por primera IP de `X-Forwarded-For`).
- Todas las dependencias están **pineadas con `==`** (política 2026-07-31; el detalle del porqué está en [arquitectura.md](arquitectura.md)).

## Convenciones de esta documentación

- Prosa en español (Perú); los términos técnicos, nombres de endpoints, variables y comandos se citan en su forma original. La UI de la aplicación está en inglés y sus pantallas/botones se citan tal como aparecen.
- Fechas siempre absolutas (AAAA-MM-DD).
- Los documentos de diseño e historia de cada cambio viven en `plan-implementacion/` (repositorio de trabajo); esta carpeta `doc/` es la referencia estable y autosuficiente.
