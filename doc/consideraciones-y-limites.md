# Consideraciones y límites del backend — Data Model Hub

Este documento describe las **consideraciones de diseño, los límites duros y las decisiones de escala** del backend de plataforma (`backend-data-model-hub`: FastAPI + Motor async sobre Azure Cosmos DB con API de Mongo). Está escrito para desarrolladores y stakeholders técnicos que necesitan entender hasta dónde aguanta la plataforma, por qué se tomaron ciertas decisiones y qué queda pendiente de endurecer.

Se basa en el código real (`app/features/reporting/query/`, `app/core/`, `app/features/auth/`, `app/features/changesets/`) y en los documentos de plan de implementación `04-STRESS-TEST`, `07-REPORTING-ENGINE` y `08-SECURITY-HARDENING`.

---

## 1. Resumen ejecutivo de límites

| Dimensión | Límite / valor | Dónde vive en el código |
|---|---|---|
| Escala validada | 10.000 tablas · 400.000 columnas · 9.178 vistas · 150 canvases · 7.336 relaciones | `scripts/seed_stress.py`, doc `04` |
| Circuit-breaker de consulta | `maxTimeMS = 15000` (15 s) | `reporting/query/executor.py` |
| Tope de página de una consulta | `QuerySpec.limit`: default 100, mínimo 1, **máximo 5000** | `reporting/query/spec.py` |
| Página interna del export | 2000 filas por lote (keyset, streaming) | `reporting/query/router.py` |
| Vida del token de sesión | JWT HS256, TTL **720 min (12 h)**, sin revocación | `core/config.py`, `core/security.py` |
| Rate limit del login | **5 requests/minuto por IP** → 429 | `features/auth/router.py` |
| Lockout de cuenta | **8 fallos consecutivos → bloqueo 15 min** | `features/auth/service.py` |
| Política de contraseña | min 10 / max 128 (al crear/cambiar, no en login) | `features/admin/schemas.py`, doc `08` |
| Clip de contraseña bcrypt | 72 bytes (determinista, documentado) | `core/security.py` |
| Límite por documento en Cosmos | 2 MB/doc — **resuelto** con un doc por cambio | `features/changesets/repository.py` |
| Pool de conexiones Motor | `maxPoolSize = 50`, `socketTimeoutMS = 60000` | `core/db/client.py` |
| Estado del rate limit | **En memoria por proceso** (no compartido entre réplicas) | `core/ratelimit.py` |

---

## 2. Escala probada (10k tablas / 400k columnas)

La prueba de estrés (`scripts/seed_stress.py`, doc `04-STRESS-TEST`) cargó data sintética al volumen objetivo (aproximadamente 15k tablas de techo) y midió los endpoints calientes con autenticación por token y RBAC activos.

### 2.1 Data cargada

| Colección | Documentos |
|---|---|
| `canonical_tables` | 10.000 |
| `canonical_columns` | 400.000 (~40 columnas por tabla) |
| `views` | 9.178 (80% de las tablas con al menos una vista) |
| `subject_areas` (canvases) | 150 (30 de ellos con 100 tablas) |
| `relationships` | 7.336 |

**Carga real:** 146 s · **0 throttles** · aproximadamente 2.600 columnas/s. El generador inserta por lotes en streaming (poca memoria) con **tolerancia a throttling** (reintenta el lote con backoff exponencial ante `429` / `code 16500`) y recrea los índices al final.

### 2.2 Latencias observadas

| Endpoint / camino | Latencia | Veredicto |
|---|---|---|
| Búsqueda de catálogo (`q` + `limit=50`) | ~190 ms | El search server-side aguanta |
| Listar todas las tablas (10k) | ~0,6–1,4 s | Aceptable |
| Canvas de 100 tablas · tablas | ~150–240 ms | Bien |
| Canvas de 100 tablas · columnas (4.000) | ~0,9–1,3 s | El lienzo más pesado, usable |
| Reporting · carga inicial (`limit=50`) | ~1,0 s | Bien, tras el fix |
| Reporting · "ver todo" (10k filas) | ~4 s | Acción explícita, poco frecuente |

### 2.3 El cuello crítico que se encontró y arregló

`GET /api/reporting/tables` tardaba **24.055 ms** a 400k columnas (inutilizable; en producción haría timeout u OOM). La causa de raíz: `report_inputs()` **materializaba las 400.000 columnas enteras** solo para contar columnas por tabla, y además cargaba los 150 canvases con sus campos pesados (`layout` / `drawings`) y las 7.336 relaciones completas.

El fix aplicó tres capas:

1. **Conteo server-side** con agregación `$group` por `tableId` sobre el índice: 400k documentos se reducen a 10k pares (24 s → ~2 s).
2. **Proyección** de `subject_areas` a `{name, projectId, tableIds}` (sin `layout`/`drawings`) y de `relationships` a `{source, target}`.
3. **Fast-path de la carga inicial**: con `limit` y sin filtros trae solo las primeras N tablas (orden físico por índice `physicalName`) más conteos, relaciones y canvases acotados a esas tablas.

Resultado: la pantalla inicial (`limit=50`) pasó de **24 s a ~1 s (24x)**.

> Lección transversal: **nunca materializar la colección completa por request**. Contar y agregar en Mongo, proyectar lo mínimo, y acotar por slice.

---

## 3. Reglas de escala del motor de reporting

El motor de reporting gira alrededor de un único contrato serializable, el **`QuerySpec`** (IR en JSON), producido por el query-builder visual o por el parser SQL, y consumido por el compilador → pipeline de Mongo, la grilla y el export. Un motor, un validador, un pushdown.

```jsonc
{
  "from": "columns",
  "select": ["physicalName", "dataType", "parentDomain", "udp.<defId>"],
  "where": { "op": "and", "conditions": [
      { "field": "schema", "op": "eq", "value": "core" },
      { "field": "udp.<defId>", "op": "in", "value": ["DAC", "Sensible"] },
      { "field": "parentDomain", "op": "isnull" }
  ]},
  "orderBy": [{ "field": "physicalName", "dir": "asc" }],
  "limit": 100, "cursor": "…"
}
```

El `op` es un **enum cerrado** (`eq, ne, in, nin, contains, startsWith, gt, gte, lt, lte, between, exists, isnull`), forzado dos veces (Pydantic `Literal` + `OPS_BY_TYPE`). El `from` solo acepta `columns | tables | relationships | views`. El cliente **nunca** manda paths de Mongo: manda una `key` pública que se resuelve contra el Field Catalog server-side.

### 3.1 Paginación keyset/seek — nunca skip profundo

A escala, `skip/limit` profundo es letal en Cosmos RU: `skip 100000` cuesta aproximadamente 1000 veces el RU (Cosmos igual barre y descarta los documentos saltados). El executor pagina por **keyset (seek)** sobre el primer campo de orden más el `_id` como desempate.

Del `executor.py`, la condición de continuación se inyecta directo al `$match`:

```python
# keyset sobre el primer campo de orden (+ _id de desempate)
sort = list(compiled.sort) or [("physicalName", 1)]
sort_path, sort_dir = sort[0]
full_sort = [(sort_path, sort_dir), ("_id", 1)]
if cursor:
    cv, cid = _decode_cursor(cursor)
    gt = "$gt" if sort_dir == 1 else "$lt"
    base_match = {"$and": [base_match, {"$or": [
        {sort_path: {gt: cv}},
        {sort_path: cv, "_id": {"$gt": cid}}]}]}
```

Se pide `limit + 1` documentos para saber si `hasMore`, y el `nextCursor` codifica `[valor_de_orden, _id]` en base64. La grilla del frontend hace scroll infinito por cursor con memoria constante aun scrolleando 400k filas.

### 3.2 Pushdown de filtros indexados

Todos los filtros bajan al `$match` que corre sobre índices. Los predicados de nivel tabla dentro de una consulta a `columns` (por ejemplo `schema`, que vive en la tabla y no en la columna) se **pre-resuelven** a un set de `tableId` con un barrido barato de las 10k tablas y se inyectan como `{tableId: {$in: [...]}}`, que sí es indexado:

```python
# _rewrite_cross_entity: schema (vive en la tabla) → tableId $in [...]
if fd and fd.entity == "table" and node.field == "schema":
    ids = [str(t["_id"]) async for t in
           db["canonical_tables"].find({**ACTIVE, "schema": {"$in": vals}}, {"_id": 1})]
    return Condition(field="tableId", op="in", value=ids or ["__none__"])
```

Solo se soporta `= / in` para el filtro cross-entity por `schema`; otros operadores se rechazan con 422.

### 3.3 El planner rechaza `.sort()` sin índice

Cosmos con API de Mongo (tier RU) **tira 500** ante un `.sort()` sobre un campo sin índice. El compilador (puro y testeado) clasifica cada orden y **rechaza** el que caería en full-scan:

```python
# Planner de orden: un row-sort exige índice (sortable) o se rechaza.
for o in spec.orderBy:
    fd = _field(catalog, o.field)
    if not fd.sortable:
        raise QueryError(
            f"No se puede ordenar por {o.field!r} (sin índice) a esta escala. "
            f"Ordená por un campo indexado (p.ej. physicalName).", code=422)
```

En cambio, cuando la consulta es agrupada (`groupBy`), el resultado es chico y se ordena en memoria por cualquier key sin restricción.

### 3.4 Sanitización de operadores de texto

`contains` y `startsWith` construyen un regex con `re.escape` — nunca un regex arbitrario del cliente, cerrando ReDoS e inyección:

```python
if op == "contains":
    return {p: {"$regex": re.escape(str(value)), "$options": "i"}}
if op == "startsWith":
    return {p: {"$regex": "^" + re.escape(str(value)), "$options": "i"}}
```

El value siempre se castea al tipo del campo (`_coerce`); los valores de UDP se fuerzan a string porque en storage todo `udpValues` es `dict[str, str]`.

### 3.5 Circuit-breaker `maxTimeMS` y tope de página

Toda operación lleva `maxTimeMS = 15000` (15 s) como corta-fuego: si una consulta se pasa, Cosmos la aborta en vez de colgar el proceso. El `QuerySpec.limit` está capeado por Pydantic entre 1 y **5000**, así que ningún cliente puede pedir una página arbitrariamente grande.

```python
MAX_TIME_MS = 15000   # executor.py
limit: int = Field(default=100, ge=1, le=5000)   # spec.py
```

### 3.6 Export por streaming (memoria O(1))

El export NUNCA hace `to_list(None)` ni construye el archivo en el browser (eso podía OOMear con 400k filas). Pagina internamente por keyset (2000 filas por lote) y hace `yield` línea por línea con `StreamingResponse`:

```python
@router.post("/export")
async def export_csv(spec: QuerySpec):
    page_spec = spec.model_copy(update={"limit": 2000})
    async def _gen():
        # ... escribe header, luego itera páginas keyset y hace yield por lote
        page = await ex.run_query(page_spec, cursor=None)
        while True:
            for row in page["rows"]:
                w.writerow(...)
            yield flush()
            if not page.get("hasMore") or not page.get("nextCursor"):
                break
            page = await ex.run_query(page_spec, cursor=page["nextCursor"])
    return StreamingResponse(_gen(), media_type="text/csv", headers=...)
```

Verificado en vivo: export CSV de 33k+ líneas sin OOM.

### 3.7 Diagrama del planner de consulta

```mermaid
flowchart TD
    A["QuerySpec JSON del cliente"] --> B["Pydantic valida: from enum, op enum, limit 1..5000, extra forbid"]
    B --> C["_rewrite_cross_entity: schema -> tableId in [...] indexado"]
    C --> D["compile_spec: resuelve field key contra Field Catalog"]
    D --> E{"Hay orderBy?"}
    E -->|campo sortable con indice| F["arma sort indexado"]
    E -->|campo sin indice| G["QueryError 422: rechazado"]
    F --> H{"Agrupada?"}
    H -->|si groupBy| I["pipeline group + project + sort en memoria"]
    H -->|no| J["keyset seek + limit+1 + maxTimeMS 15s"]
    I --> K["rows + columns"]
    J --> K
```

---

## 4. Límites de Cosmos DB (API de Mongo)

El backend corre sobre **Azure Cosmos DB con API de Mongo**. El código está escrito defensivamente para las restricciones del **tier basado en RU (Request Units)**, que son las que dictan la mayoría de las decisiones de escala.

### 4.1 RU y throttling (429 / code 16500)

Cuando el consumo de RU supera el throughput provisionado, Cosmos responde **429** (en Mongo API suele aparecer como `code 16500`). El generador de estrés lo maneja con backoff exponencial:

- Las latencias observadas varían con el throughput provisionado (RU compartido). Con más RU, todo baja proporcionalmente.
- La carga de estrés (146 s, ~2.600 columnas/s) corrió **sin ningún throttle**, insertando por lotes en streaming.

Consideración operativa: en producción, dimensionar el RU (o autoscale) según la carga esperada del reporting y del canvas. Los picos de `POST /query` sobre `canonical_columns` (400k docs) son los más caros.

### 4.2 `.sort()` requiere índice

En el tier RU, ordenar por un campo **sin índice** devuelve un error del servidor. Esto obliga a:

- El planner del reporting rechaza el `orderBy` no indexado (sección 3.3).
- La búsqueda de catálogo ordena por `physicalName`, que tiene índice dedicado.
- El repositorio de changesets solo acepta `sort_field` si hay índice; sin él, ordena en Python tras el corte.

```python
# core/db/indexes.py — comentario del índice de physicalName
# REQUERIDO por la búsqueda server-side del catálogo (?q=&limit=): el top-N
# se ordena en Mongo por physicalName, y Cosmos RU rechaza .sort() sobre
# campos sin índice (500 en todas las búsquedas).
```

### 4.3 Índice wildcard `udpValues.$**`

Los UDP (User Defined Properties) son columnas dinámicas que el usuario crea en runtime. No se puede crear un índice por cada key nueva sin DDL constante. La solución es un **índice wildcard** sobre el mapa embebido, en tablas y columnas:

```python
_try("canonical_columns", [("udpValues.$**", 1)]),
_try("canonical_tables",  [("udpValues.$**", 1)]),
```

Cubre todas las keys UDP presentes **y futuras**: `eq`, `$in` e `$exists` hacen seek. **Limitación load-bearing:** como `udpValues` es `dict[str, str]` (todos los valores son STRING, aun para UDP number/date), los operadores `=`, `IN` e `IS NULL` son de primera clase (index-backed); el **rango** (`>`, `<`, `BETWEEN`) sobre UDP no-string es best-effort (comparación léxica). El índice wildcard tampoco sirve para **ordenar** — el sort debe usar un campo indexado normal.

### 4.4 Otros comportamientos tolerados

Cosmos con API de Mongo puede levantar `NamespaceExists` (code 48) si la colección se creó implícitamente antes de la llamada al índice, y duplicate-key (11000) en índices únicos ya existentes. `ensure_indexes` traga puntualmente esos dos códigos (el índice igual queda bien creado):

```python
if code not in (48, 11000):
    raise
```

### 4.5 Timeouts de conexión

El cliente Motor se configura para que un socket colgado no bloquee el request por el default del SO (minutos) ni agote el pool:

```python
AsyncIOMotorClient(
    settings.COSMOS_CONNECTION_STRING,
    serverSelectionTimeoutMS=15_000,
    connectTimeoutMS=10_000,
    socketTimeoutMS=60_000,
    maxPoolSize=50,
)
```

### 4.6 Índices declarados

| Colección | Índices |
|---|---|
| `canonical_tables` | `flgactive`, `physicalName`, `udpValues.$**` |
| `canonical_columns` | `tableId`, `parentDomainId`, `physicalName`, `dataType`, `udpValues.$**` |
| `changesets` | `updatedAt` (desc), `status` |
| `changeset_changes` | compuesto `(csId, collection)` |
| `relationships` | `flgactive`, `sourceTableId`, `targetTableId` |
| `views` | `flgactive`, `tableId` |
| `subject_areas` | `projectId` |
| `folders` | `projectId` |
| `naming_config` | `scope` |
| `users` | `email` |
| `audit_log` | `at` (desc), `actor` |
| `standards_versions` | `seq` (**unique**) |
| `parent_domains`, `glossary_terms`, `udp_definitions`, `projects` | `flgactive` |

---

## 5. Límites de autenticación y sesión

La auth es propia (usuario/contraseña) en todos los entornos. La identidad sale de un token de sesión firmado (Bearer, sin cookies), no de headers.

### 5.1 JWT de 12 h sin revocación

El token es JWT **HS256** con `algorithms=[HS256]` fijo (sin alg-confusion) y TTL de **720 minutos (12 h)** por default:

```python
ACCESS_TOKEN_TTL_MIN: int = int(os.getenv("ACCESS_TOKEN_TTL_MIN", "720"))
```

**Límite conocido (pendiente HIGH):** no hay denylist ni `tokenVersion`. Deshabilitar un usuario en Admin **no corta su sesión** hasta que el token expira, en los endpoints que solo dependen de `current_principal`. Nota: `resolve_session_user` (usado por `/me` y el gating del frontend) sí devuelve `None` si el usuario está `disabled`, pero eso no invalida un token ya emitido a nivel transporte. Mitigaciones recomendadas: bajar el TTL a 15–30 min con refresh revocable, un `tokenVersion` por usuario, o re-validar `status != disabled` contra la DB en cada request.

### 5.2 Falla-cerrado del `SECRET_KEY`

En postura de producción (`REQUIRE_AUTH=true`), la app **no arranca** si el `SECRET_KEY` sigue siendo el default de desarrollo (con esa clave pública cualquiera forjaría un token admin):

```python
if using_default and settings.REQUIRE_AUTH:
    raise RuntimeError(
        "SECRET_KEY inseguro con REQUIRE_AUTH=true: definí SECRET_KEY "
        "antes de desplegar — con el default público se pueden forjar tokens admin.")
```

### 5.3 Rate limit del login: 5/minuto por IP

```python
@router.post("/login")
@limiter.limit("5/minute")  # anti fuerza bruta / credential stuffing (por IP)
async def login(request: Request, body: LoginBody): ...
```

El 6º intento en un minuto responde **429** con `Retry-After`. Sin límite global (el canvas y el reporting disparan muchos requests legítimos). Verificado en vivo: el 6º intento devuelve 429.

### 5.4 Lockout: 8 fallos → 15 min

Complementa el rate limit por IP contra ataques distribuidos o botnets. Tras 8 fallos consecutivos de una cuenta **existente y activa**, se bloquea 15 minutos con un `$inc` atómico de `failedAttempts`:

```python
_LOCK_THRESHOLD = 8
_LOCK_MINUTES = 15
```

Solo se cuentan fallos de cuentas existentes (no se crea documento para usuarios inexistentes → sin oráculo de enumeración por lockout). El login exitoso resetea el contador y quita el bloqueo.

### 5.5 Anti-enumeración por timing

`login()` verifica el hash **siempre**, aun si el usuario no existe, usando un `_DUMMY_HASH` bcrypt real. Con `bcrypt.checkpw` en tiempo constante y un 401 genérico, no se filtra por timing qué usuarios existen. Limitación menor documentada: bcrypt **clipa a 72 bytes** de forma determinista.

### 5.6 Flujo del login con las defensas

```mermaid
sequenceDiagram
    participant C as Cliente
    participant RL as Rate limiter 5 por minuto IP
    participant S as service.login
    participant DB as Cosmos users
    C->>RL: POST /api/auth/login
    alt supera 5 por minuto
        RL-->>C: 429 Retry-After
    else dentro del limite
        RL->>S: username password
        S->>DB: get_login_record
        alt cuenta bloqueada lockedUntil futuro
            S-->>C: 401 audita login_locked
        else
            S->>S: verify_password constante DUMMY si no existe
            alt credencial invalida
                S->>DB: register_failed_login inc atomico
                Note over S,DB: 8 fallos entonces bloquea 15 min
                S-->>C: 401 generico
            else credencial valida
                S->>DB: clear_failed_login
                S-->>C: 200 token JWT 12h user
            end
        end
    end
```

### 5.7 Reporting con lecturas abiertas

`POST /query`, `POST /query/sql`, `GET /facets`, `POST /export` y `GET /insights/*` **no dependen de `current_principal`**: son lecturas abiertas, gateadas globalmente por el login del entorno de producción. Solo el CRUD de `saved_reports` exige principal (y fija el `owner` server-side, sin mass assignment). Pendiente recomendado: gatear también las lecturas de reporting para reducir la superficie de DoS sobre las colecciones de 400k.

---

## 6. Changesets: un documento por cambio (límite de 2 MB resuelto)

Cada cambio de modelado vive en la colección `changeset_changes` como **un documento por cambio**, con `_id` determinista `{csId}::{collection}::{entityId}`. El diseño viejo embebía todos los cambios en un dict dentro del documento del changeset y topaba el **límite de 2 MB por documento** de Cosmos con changesets grandes.

```python
def change_key(cs_id: str, collection: str, entity_id: str) -> str:
    # _id determinista → upsert por _id = last-write-wins por entidad, sin duplicados
    return f"{cs_id}::{collection}::{entity_id}"
```

El documento de `changesets` queda como cabecera (estado, decisiones, comentarios). Consecuencias de diseño:

- El guard de estado ya no puede vivir en el filtro de una sola escritura (estado y cambio están en documentos distintos), así que `set_change` usa un **protocolo de 3 pasos con compensación** (touch atómico del padre con `status: draft`, upsert del cambio con `wtoken` único, re-check y compensación si un submit ganó la carrera).
- Las listas de versiones/requests proyectan **solo cabeceras** (`{"changes": 0, "comments": 0}`); bajar el blob `changes` de un changeset grande por fila no escala.
- El publish aplica el plan con **un `bulk_write` por colección** (`ordered=False`) en vez de un `update_one` awaiteado por entidad, que antes tardaba minutos con miles de cambios y dejaba una ventana enorme de aplicación parcial.

### 6.1 Validación de payloads (round-trip garantizado)

Un upsert de changeset termina aplicándose tal cual (`$set` del payload) a la colección publicada. Sin un gate, un payload malformado (columna sin `tableId`, relación sin extremos) entraría a producción y rompería a todos los lectores. Se valida contra los mismos modelos `*Doc` del read path, en dos puntos: a la entrada (`add_change`, feedback inmediato) y a la salida (`_apply_and_finalize`, gate autoritativo justo antes de escribir).

```mermaid
flowchart TD
    A["Cliente edita entidad"] --> B["add_change: valida payload contra Doc model"]
    B -->|invalido| C["422 mensaje legible"]
    B -->|valido| D["set_change: upsert 1 doc en changeset_changes id determinista"]
    D --> E["submit: changeset pasa a submitted"]
    E --> F["revisores aprueban"]
    F --> G["_apply_and_finalize: re-valida payloads gate autoritativo"]
    G --> H["apply_changes: 1 bulk_write por coleccion ordered false"]
    H --> I["colecciones publicadas actualizadas"]
```

### 6.2 Alcance versionado

Solo pasan por el changeset/aprobación del canvas: `canonical_tables`, `canonical_columns`, `relationships`, `views`. Los estándares (Parent Domains, Glossary, UDP definitions) se editan y versionan aparte, en el módulo Data Standards, con escritura global directa fuera del publish.

---

## 7. Consideraciones operativas

### 7.1 Rate limit en memoria por proceso → Redis para multi-réplica

El estado del rate limit (slowapi) es **en memoria por proceso**. Con una sola instancia es suficiente, pero en un despliegue multi-réplica cada réplica cuenta por separado y el límite efectivo se multiplica por el número de réplicas.

```python
# core/ratelimit.py
# ESTADO EN MEMORIA (por proceso): suficiente para una sola instancia. En
# producción multi-réplica migrar a un backend Redis
# (Limiter(storage_uri="redis://...") / fastapi-limiter).
```

El rate limit está **gateado por postura**: activo en producción (`REQUIRE_AUTH`) o con `RATE_LIMIT_ENABLED=true`; apagado en dev/tests para no frenar el harness (muchos logins desde localhost) ni el uso local.

### 7.2 Postura de seguridad HTTP

Configurado en `main.py`:

- **Security headers** en toda respuesta: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy` (geolocation/microphone/camera deshabilitados), y `Strict-Transport-Security` solo sobre HTTPS (detectado por `X-Forwarded-Proto` detrás de proxy).
- **CORS estricto**: `allow_credentials=False`, métodos y headers explícitos (no wildcard), allowlist de orígenes desde `CORS_ORIGINS`.
- **`/docs`, `/redoc`, `/openapi.json` ocultos** cuando `REQUIRE_AUTH` (reduce fingerprinting).
- **TrustedHost** si `ALLOWED_HOSTS` está definido.
- Excepciones no controladas → envelope de error genérico (sin stack trace al cliente), con `X-Request-ID` para correlacionar en logs.

### 7.3 Costos inherentes que quedan (mejoras futuras)

- **Reporting "ver todo" (~4 s):** costo inherente de agregar 400k columnas + leer 10k tablas. Si molesta, paginar server-side por `physicalName`.
- **Export sin filtro** (`/reporting/columns` sin `tableIds`): serializa las 400k columnas (~100 MB). Es una acción de export masivo deliberada; el front debería acotarla por selección. El export nuevo del motor de consulta ya va por streaming.
- **Canvas de 100 tablas (~1–1,5 s, 4.000 columnas):** aceptable; para canvases aún mayores, virtualizar el detalle de columnas.
- **UDP coverage** se computa on-demand (~1 s, barato por el `$match udpValues != {}`); materializar en `report_udp_coverage` solo si el volumen de UDP-values crece mucho.
- **Rango sobre UDP string:** best-effort con `$convert`, reportando el count de descartados; no se migra el storage en v1.
- **Contraseñas filtradas** (HIBP / zxcvbn) y migración bcrypt → argon2id: pendientes de menor prioridad.

---

## 8. Despliegue: dónde corre, variables de entorno y base de datos

Este documento no cubre CI/CD ni pipelines. Solo describe **dónde corre el backend**, su **configuración por entorno** y las **consideraciones de base de datos**.

### 8.1 Dónde corre

El backend es una app FastAPI (`entry: app.main:app`, servida con uvicorn) y puede desplegarse en:

- **Databricks Apps** (destino primario según el README; runtime Python 3.11). Bundle `databricks.yml` + `app.yaml`.
- **Azure App Service** como alternativa de hosting equivalente (mismo entrypoint uvicorn/ASGI).

En ambos casos la app es un **monolito modular** (`app/core/` para infra compartida + `app/features/<x>/` como vertical slices), stateless salvo por el rate limit en memoria (ver 7.1). La conexión a Cosmos se abre/cierra en el lifespan de FastAPI y asegura los índices al arrancar.

### 8.2 Variables de entorno

| Variable | Propósito | Default / nota |
|---|---|---|
| `COSMOS_CONNECTION_STRING` | Cadena de conexión a Cosmos (API de Mongo). **Requerida**; sin ella el arranque levanta `RuntimeError` | vacía |
| `COSMOS_DATABASE` | Nombre de la base | `db_modeler` |
| `SECRET_KEY` | Clave HMAC para firmar el JWT de sesión. **Obligatoria en producción** | default inseguro solo dev; falla-cerrado con `REQUIRE_AUTH` |
| `REQUIRE_AUTH` | Postura de producción: request sin token válido → 401; oculta `/docs`; activa rate limit | `false` |
| `ACCESS_TOKEN_TTL_MIN` | Vida del token de sesión en minutos | `720` (12 h) |
| `RATE_LIMIT_ENABLED` | Fuerza el rate limit aun sin `REQUIRE_AUTH` | `false` |
| `ALLOWED_HOSTS` | Allowlist de Host (activa TrustedHost) | vacío (sin restricción) |
| `CORS_ORIGINS` | Orígenes del frontend permitidos (coma-separados) | `http://localhost:3000`, `http://127.0.0.1:3000` |
| `AUTH_MODE` | Seam de identidad (`local` / `databricks`), por compat | `local` |
| `LOG_FORMAT` / `LOG_LEVEL` | Formato (`pretty` dev / `json` prod) y nivel de log | — |

Checklist mínimo de producción: `COSMOS_CONNECTION_STRING` + `SECRET_KEY` fuerte + `REQUIRE_AUTH=true` + `CORS_ORIGINS` del frontend real + `ALLOWED_HOSTS`. Con `REQUIRE_AUTH=true` y `SECRET_KEY` default, la app **no arranca** (por diseño).

### 8.3 Consideraciones de base de datos (Azure Cosmos DB con API de Mongo)

- Un único cluster Cosmos y una única database (`db_modeler`), compartidos con el servicio de agentes (`app-agents-modeler`), sobre **colecciones disjuntas**: este backend administra `canonical_tables`, `canonical_columns`, `relationships`, `views`, `subject_areas`, `folders`, `changesets`, `changeset_changes`, `parent_domains`, `glossary_terms`, `udp_definitions`, `standards_versions`, `users`, `roles`, `audit_log`, `saved_reports`, `naming_config`; el agente administra `column_catalog`. El seed de estrés **nunca toca** `column_catalog`.
- **Dimensionar el RU** (o habilitar autoscale) según la carga del reporting y del canvas. Los `POST /query` sobre `canonical_columns` (400k documentos) son las operaciones más caras; sin RU suficiente aparecen 429 (`code 16500`).
- Los **índices se aseguran al arrancar** (`ensure_indexes`, idempotente). El índice wildcard `udpValues.$**` es crítico para filtrar por UDP creados en runtime y debe existir después del bulk load inicial (por ejemplo el import one-shot de Erwin).
- La app tolera `NamespaceExists` (48) y duplicate-key (11000) al crear índices; cualquier otro error de índice sí propaga.
- Los timeouts del cliente (`serverSelectionTimeoutMS=15s`, `connectTimeoutMS=10s`, `socketTimeoutMS=60s`, `maxPoolSize=50`) evitan que un socket colgado agote el pool.

### 8.4 Topología de referencia

```mermaid
flowchart TD
    FE["Frontend Vite Next"] -->|Bearer token /api| BE["backend-data-model-hub FastAPI uvicorn"]
    BE -->|Motor async| COS["Azure Cosmos DB API de Mongo db_modeler"]
    AG["app-agents-modeler"] -->|column_catalog| COS
    subgraph Hosting
        BE
    end
    subgraph Notas
        N1["REQUIRE_AUTH true SECRET_KEY fuerte"]
        N2["rate limit en memoria por proceso migrar a Redis si multi replica"]
        N3["RU dimensionado indices wildcard udpValues asegurados al boot"]
    end
```

---

## 9. Cuadro final de límites y mitigaciones pendientes

| Área | Límite actual | Estado | Mitigación futura |
|---|---|---|---|
| Reporting a escala | 400k columnas, keyset + planner + maxTimeMS 15s + limit 5000 | Resuelto | Paginar "ver todo" server-side si molesta |
| Export masivo | Streaming O(1), 2000 filas/lote | Resuelto | Job async + poll para rangos enormes |
| JWT | TTL 12 h, sin revocación | Pendiente HIGH | Bajar TTL + refresh revocable o `tokenVersion` |
| Rate limit | En memoria por proceso | Aceptable 1 instancia | Backend Redis para multi-réplica |
| Lecturas de reporting | Abiertas (sin principal) | Pendiente | Gatear con `current_principal` |
| Rango sobre UDP | Léxico (values son string) | Documentado | `$convert` tipado + reportar descartados |
| Changesets 2 MB/doc | Un doc por cambio | Resuelto | — |
| Cosmos RU | 429 bajo carga alta | Operativo | Dimensionar RU / autoscale |
| Contraseñas | min 10 / max 128 | Aceptable | HIBP/zxcvbn, argon2id |

La postura general es sólida: el motor de reporting es defensivo por diseño (allowlist de campos, enum cerrado de operadores, `re.escape`, planner que rechaza scans y sorts sin índice, cursor validado a escalares), la escala está probada a 10k tablas / 400k columnas, y el login es criptográficamente correcto con rate limit + lockout. Los pendientes de mayor impacto son la **revocación de JWT** y el **rate limit con Redis** para operación multi-réplica.
