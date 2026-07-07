# Políticas de seguridad del backend — Data Model Hub

Este documento describe la postura de seguridad del backend de plataforma (`backend-data-model-hub`), construido con FastAPI y Motor sobre Azure Cosmos DB (API de Mongo). Cubre todo lo implementado hasta el hardening del 2026-07-06: autenticación con contraseña propia, firma de sesión con JWT, defensas contra fuerza bruta, cabeceras de seguridad, CORS y host allowlist, control de acceso basado en roles (RBAC), endurecimiento del motor de reporting frente a inyección, y auditoría. Cierra con la tabla resumen de mitigaciones, el estado de lo pendiente y las consideraciones de despliegue.

La audiencia es doble: desarrolladores que mantienen el servicio y stakeholders técnicos que necesitan entender la postura de riesgo. Todo lo que sigue está verificado contra el código real; cuando decimos "falla-cerrado" o "tiempo constante" es porque el código lo hace, no porque suene bien.

---

## 1. Principios de diseño

El backend adopta cuatro principios transversales que explican casi todas las decisiones concretas más abajo:

1. **Falla-cerrado en producción.** La postura de producción se activa con la variable `REQUIRE_AUTH=true`. Con ella, la app no arranca si sigue usando la clave de firma de desarrollo, exige login en toda request sin token, oculta la documentación interactiva y activa el rate limiting. Si algo no está bien configurado, el sistema prefiere no arrancar o rechazar antes que abrir un hueco.
2. **Identidad token-first.** La identidad del usuario sale del token de sesión firmado (`Authorization: Bearer …`), no de cabeceras manipulables por el cliente. En desarrollo local hay un fallback conmutable, pero en producción ese fallback está apagado.
3. **Superficie mínima.** CORS con allowlist explícita (sin comodines), métodos y cabeceras acotados, documentación oculta en producción, host allowlist opcional, y mensajes de error genéricos que no filtran stack traces.
4. **Defensa en profundidad.** El rate limiting por IP y el lockout por cuenta se complementan; el RBAC gatea a nivel de router y de endpoint; el motor de reporting valida el spec en varias capas aunque una sola bastaría.

```mermaid
flowchart TD
    A[Postura de produccion REQUIRE_AUTH true] --> B[assert_secure_config bloquea SECRET_KEY default]
    A --> C[current_principal exige token 401 sin sesion]
    A --> D[docs redoc openapi ocultos]
    A --> E[rate limiting del login activo]
    A --> F[HSTS sobre HTTPS]
```

---

## 2. Autenticación

La autenticación es propia: usuario y contraseña contra la colección `users` de Cosmos, con el hash guardado por bcrypt y una sesión emitida como JWT firmado. No hay dependencia de un IdP externo para autenticar (el modo Databricks solo derivaría identidad de cabeceras SSO si se reactivara, pero hoy no se usa para autenticar).

### 2.1 Hash de contraseñas: bcrypt en tiempo constante

El hashing vive en `app/core/security.py`. Se usa bcrypt con salt aleatorio embebido y comparación en tiempo constante:

```python
def hash_password(password: str) -> str:
    return bcrypt.hashpw(_clip(password), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_clip(password), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
```

Detalles que importan:

- **Comparación en tiempo constante.** `bcrypt.checkpw` compara sin cortocircuitar por el primer byte distinto, lo que evita ataques de temporización sobre la comparación del hash.
- **Clip determinista a 72 bytes.** bcrypt trunca a 72 bytes; en vez de un truncamiento silencioso, `_clip` lo hace explícito y documentado (`password.encode("utf-8")[:72]`). El mismo texto produce siempre el mismo resultado de hash y de verificación.
- **Tolerancia a hashes inválidos.** Si el hash está vacío o corrupto (por ejemplo, un usuario legacy sin `passwordHash`), `verify_password` devuelve `False` en vez de lanzar una excepción que tumbaría el login.

### 2.2 Anti-enumeración por temporización: dummy hash

El servicio de login (`app/features/auth/service.py`) verifica **siempre** un hash bcrypt, aun cuando el usuario no exista, usando un hash señuelo calculado al importar el módulo:

```python
_DUMMY_HASH = hash_password("__no_such_user__")
```

En el login, si no hay registro para el usuario, se verifica la contraseña contra `_DUMMY_HASH`. Sin esto, un usuario inexistente respondería más rápido (no habría hash que verificar) y un atacante podría enumerar qué usuarios existen midiendo el tiempo de respuesta. Con el dummy, el costo de CPU es equivalente exista o no la cuenta.

Además, el lockout por intentos fallidos **solo** cuenta fallos de cuentas existentes y activas: no se crea documento para usuarios inexistentes, de modo que el lockout tampoco se convierte en un oráculo de existencia.

### 2.3 Sesión firmada: JWT HS256

El token de sesión es un JWT firmado con HMAC-SHA256 (`app/core/security.py`):

```python
def create_access_token(subject, *, extra=None, ttl_min=None, now=None) -> str:
    payload = {
        "sub": subject,                         # username
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl)).timestamp()),
        **(extra or {}),                        # email, name, role
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")

def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
```

Propiedades de seguridad:

- **Algoritmo fijado en la validación.** `algorithms=["HS256"]` impide el ataque de confusión de algoritmos (por ejemplo, un token forjado con `alg=none` o intentos de degradación). El validador no acepta otro algoritmo.
- **Expiración obligatoria.** El TTL por defecto es 720 minutos (12 horas, una jornada de trabajo), configurable con `ACCESS_TOKEN_TTL_MIN`. `jwt.decode` valida `exp` automáticamente y devuelve `None` si expiró.
- **Nunca levanta.** `decode_access_token` atrapa todo error de PyJWT y devuelve `None`; es el caller quien decide el 401. Esto evita que un token malformado produzca un 500.
- **El `sub` es el username**, que a su vez es la clave del actor (`users._id`), lo que se compara contra `reviewers[]` y lo que audita cada acción.

Contenido típico del payload decodificado:

```json
{
  "sub": "ana.torres",
  "iat": 1751850000,
  "exp": 1751893200,
  "email": "ana.torres@empresa.com",
  "name": "Ana Torres",
  "role": "modelador"
}
```

### 2.4 Falla-cerrado en el arranque: `assert_secure_config`

La clave de firma tiene un default público **solo para desarrollo local** (`INSECURE_DEFAULT_SECRET_KEY = "dev-only-insecure-change-me-in-prod"`). Si en producción ese default sigue en uso, cualquiera podría forjar un JWT de administrador. Por eso `create_app()` llama a `assert_secure_config()` antes de construir la app (`app/core/config.py`):

```python
def assert_secure_config() -> None:
    using_default = settings.SECRET_KEY == INSECURE_DEFAULT_SECRET_KEY
    if using_default:
        if settings.REQUIRE_AUTH:
            raise RuntimeError(
                "SECRET_KEY inseguro con REQUIRE_AUTH=true: definí SECRET_KEY "
                "(env/secreto) antes de desplegar — con el default público se "
                "pueden forjar tokens de sesión admin."
            )
        log.warning("SECRET_KEY usa el default de desarrollo (INSEGURO). ...")
```

Es decir: con `REQUIRE_AUTH=true` y la clave por defecto, la app **no arranca**. En desarrollo, aunque no bloquea, imprime una advertencia fuerte para que nadie lo olvide antes de desplegar.

### 2.5 Derivación de identidad: token-first, con fallback gateado

El seam de identidad (`app/core/identity/dependencies.py`) resuelve el usuario en sesión con la dependencia `current_principal`:

```python
def current_principal(request: Request) -> Principal:
    token = bearer_token(request)
    if token is not None:
        claims = decode_access_token(token)
        if claims and claims.get("sub"):
            return _principal_from_claims(claims)      # source="session"
        raise HTTPException(401, "Sesión inválida o expirada. Iniciá sesión de nuevo.")
    if settings.REQUIRE_AUTH:
        raise HTTPException(401, "Autenticación requerida. Iniciá sesión.")
    return get_identity_provider().principal_from_request(request)
```

Reglas:

- **Token presente y válido:** identidad `source="session"`, derivada de los claims firmados.
- **Token presente pero inválido o expirado:** siempre 401. No se cae al fallback, porque el cliente afirmó tener una sesión; degradar a un usuario anónimo sería un agujero.
- **Sin token y `REQUIRE_AUTH=true` (producción):** 401, login obligatorio.
- **Sin token y `REQUIRE_AUTH=false` (dev/tests):** fallback al seam de identidad (`AUTH_MODE`: usuario fake local, cabecera `X-Dev-User` para actuar como otro usuario en pruebas, o cabeceras OBO de Databricks). Esto permite desarrollar y correr tests que no montan DB sin exigir login.

```mermaid
flowchart TD
    A[Request entrante] --> B{Header Authorization Bearer presente?}
    B -->|Si| C{Token valido y con sub?}
    C -->|Si| D[Principal source session]
    C -->|No| E[401 sesion invalida o expirada]
    B -->|No| F{REQUIRE_AUTH activo?}
    F -->|Si| G[401 autenticacion requerida]
    F -->|No| H[Fallback al seam de identidad local o databricks]
```

### 2.6 Endpoints de auth

Definidos en `app/features/auth/router.py`:

| Método y ruta | Descripción | Respuesta |
|---|---|---|
| `POST /api/auth/login` | Login usuario/contraseña. Rate-limited `5/minute` por IP. | `{token, user}` o 401 |
| `POST /api/auth/logout` | Cierra sesión (audita `logout`). | `{ok: true}` |
| `GET /api/auth/me` | Usuario en sesión enriquecido con rol y permisos efectivos. | usuario o Principal básico |

Ejemplo de login exitoso:

```bash
curl -sX POST https://data-model-hub.example/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"ana.torres","password":"Contrasena-larga-2026"}'
```

```json
{
  "success": true,
  "data": {
    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhbmEudG9ycmVzIn0...",
    "user": {
      "username": "ana.torres",
      "email": "ana.torres@empresa.com",
      "name": "Ana Torres",
      "role": "modelador",
      "roleName": "Modelador",
      "permissions": { "model.view": true, "model.edit": true, "publish": false, "admin.manage": false },
      "accessLevel": "edit"
    }
  }
}
```

Nota importante: el objeto `user` **nunca** incluye `passwordHash`. El repositorio (`app/features/auth/repository.py`) solo lo lee en la ruta de login (`get_login_record`, con proyección explícita) y el conversor `_to_user` lo excluye de toda respuesta al cliente.

### 2.7 Flujo completo de login (con defensas)

```mermaid
sequenceDiagram
    participant C as Cliente
    participant R as Router auth
    participant L as slowapi Limiter
    participant S as Service login
    participant DB as Cosmos users
    C->>R: POST /api/auth/login
    R->>L: chequear 5 por minuto por IP
    alt limite excedido
        L-->>C: 429 Retry-After
    else dentro del limite
        R->>S: login username password
        S->>DB: get_login_record hash status contadores
        alt cuenta bloqueada por lockout
            S->>DB: audit login_locked
            S-->>C: 401 generico
        else no bloqueada
            S->>S: verify_password con dummy hash si no existe
            alt credenciales invalidas
                S->>DB: register_failed_login inc atomico
                S->>DB: audit login_failed
                S-->>C: 401 generico
            else credenciales validas
                S->>DB: clear_failed_login
                S->>S: create_access_token HS256
                S->>DB: audit login
                S-->>C: 200 token y user
            end
        end
    end
```

---

## 3. Rate limiting del login

Contra fuerza bruta y credential stuffing, el login lleva un límite por IP con slowapi (`app/core/ratelimit.py` y el decorator en el router):

```python
# app/features/auth/router.py
@router.post("/login")
@limiter.limit("5/minute")   # anti fuerza bruta / credential stuffing (por IP)
async def login(request: Request, body: LoginBody): ...
```

```python
# app/core/ratelimit.py
_ENABLED = settings.REQUIRE_AUTH or os.getenv("RATE_LIMIT_ENABLED", "").lower() in ("1", "true", "yes")
limiter = Limiter(key_func=get_remote_address, headers_enabled=True, enabled=_ENABLED)
```

Características:

- **Clave = IP del cliente** (`get_remote_address`).
- **Solo por-ruta, no global.** El canvas y el reporting disparan muchos requests legítimos; un límite global los frenaría. El límite estricto va donde importa: el login.
- **Gateado por postura.** Activo en producción (`REQUIRE_AUTH`) o cuando se fuerza con `RATE_LIMIT_ENABLED=true`. En dev y tests queda apagado por defecto para no frenar el harness (muchos logins desde localhost) ni el uso local.
- **Respuesta 429 con `Retry-After`.** El handler `_rate_limit_exceeded_handler` se registra en `main.py`; con `headers_enabled=True` se agregan las cabeceras de límite.

Ejemplo de respuesta al sexto intento dentro del minuto:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 47
X-RateLimit-Limit: 5
X-RateLimit-Remaining: 0
```

**Limitación conocida:** el estado del limiter es en memoria por proceso. En un despliegue multi-réplica, cada instancia cuenta por separado y el límite efectivo se multiplica por el número de réplicas. Ver la sección de pendientes (Redis).

---

## 4. Lockout por intentos fallidos

Complementando el rate limiting por IP (que un atacante distribuido con muchas IPs podría sortear), hay un lockout por cuenta en `app/features/auth/service.py`:

```python
_LOCK_THRESHOLD = 8    # fallos consecutivos
_LOCK_MINUTES = 15     # duración del bloqueo
```

Comportamiento:

- Tras **8 fallos consecutivos**, la cuenta se bloquea **15 minutos**.
- El contador se incrementa de forma **atómica** con `$inc` (`register_failed_login` en el repositorio), evitando condiciones de carrera bajo requests concurrentes.
- Al alcanzar el umbral, se fija `lockedUntil` (ISO-8601 UTC). El login compara lexicográficamente contra `datetime.now(...).isoformat()`, correcto porque ambos usan el mismo formato.
- Un login exitoso **resetea** el contador y quita el bloqueo (`clear_failed_login`).
- Se auditan las tres situaciones: `login`, `login_failed`, `login_locked`.
- **Sin oráculo de existencia:** solo se registran fallos para cuentas existentes y no deshabilitadas. Un atacante no puede distinguir "usuario no existe" de "contraseña incorrecta" ni por temporización (dummy hash) ni por comportamiento de lockout.

Los campos de lockout (`failedAttempts`, `lockedUntil`) son internos: se leen con proyección explícita en `get_login_record` y **no** pasan por el modelo `UserDoc`, por lo que nunca se exponen al frontend.

---

## 5. Política de contraseñas

Definida en los DTOs del módulo Admin (`app/features/admin/schemas.py`):

```python
_PW_MIN, _PW_MAX = 10, 128

class UserCreate(BaseModel):
    ...
    password: str = Field(min_length=_PW_MIN, max_length=_PW_MAX)

class UserUpdate(BaseModel):
    ...
    password: str | None = Field(default=None, min_length=_PW_MIN, max_length=_PW_MAX)
```

- **Mínimo 10, máximo 128 caracteres.** El mínimo sube el costo de fuerza bruta; el máximo evita entradas absurdas y hace explícito el clip de bcrypt a 72 bytes.
- **Se valida al crear o cambiar,** no en el login. `LoginBody` no impone longitud, porque el login debe aceptar cualquier valor para poder verificarlo (si lo rechazara, filtraría información sobre la política a un atacante y rompería cuentas legacy).
- La validación ocurre en el borde (Pydantic), antes de llegar a la lógica de negocio.

---

## 6. Cabeceras de seguridad

Todas las respuestas pasan por el middleware de logging en `app/main.py`, que agrega cabeceras de defensa en profundidad:

| Cabecera | Valor | Propósito |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | Impide que el browser adivine el tipo MIME (anti MIME-sniffing). |
| `X-Frame-Options` | `DENY` | Bloquea el embebido en iframes (anti clickjacking). |
| `Referrer-Policy` | `no-referrer` | No filtra la URL de origen en navegaciones salientes. |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` | Desactiva APIs sensibles del browser. |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | Fuerza HTTPS por un año. **Solo se envía sobre HTTPS.** |
| `X-Request-ID` | id de 12 hex | Correlación de logs para diagnóstico. |

Snippet relevante:

```python
response.headers["X-Content-Type-Options"] = "nosniff"
response.headers["X-Frame-Options"] = "DENY"
response.headers["Referrer-Policy"] = "no-referrer"
response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
```

HSTS solo se emite sobre HTTPS para no romper el desarrollo local en HTTP. Detrás de un proxy (Azure App Service, Databricks Apps), el esquema real se detecta por `X-Forwarded-Proto`.

---

## 7. CORS estricto

Configurado en `create_app()` (`app/main.py`) con allowlist explícita, sin comodines:

```python
cors_origins = (
    [o.strip() for o in cors_env.split(",") if o.strip()]
    if cors_env else ["http://localhost:3000", "http://127.0.0.1:3000"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    max_age=600,
)
```

Decisiones:

- **Orígenes de allowlist.** En producción se define con `CORS_ORIGINS` (lista separada por comas); el default apunta al frontend local.
- **`allow_credentials=False`.** La auth es token-first (Bearer, sin cookies), así que no se necesitan credenciales cross-origin. Apagarlo reduce el riesgo de CSRF por cookies y permite mantener la allowlist estricta sin la interacción problemática entre `allow_credentials=True` y comodines.
- **Métodos y cabeceras acotados** en vez de `*`: superficie mínima. Solo se aceptan `Authorization`, `Content-Type` y `X-Requested-With`.

Nota sobre errores: el handler global de `Exception` corre en el middleware más externo (`ServerErrorMiddleware`), **fuera** de `CORSMiddleware`. Por eso, en un 500, el backend re-agrega manualmente `Access-Control-Allow-Origin` (solo si el `Origin` está en la allowlist) para que el frontend pueda leer el envelope de error en vez de un opaco "Failed to fetch". Es una excepción controlada que **no** amplía la política CORS: valida el origen contra la misma lista.

---

## 8. TrustedHost y documentación oculta en producción

### 8.1 Host allowlist

Si `ALLOWED_HOSTS` está definido (producción), se monta `TrustedHostMiddleware`, que rechaza requests cuyo header `Host` no esté en la lista, mitigando ataques de Host header:

```python
allowed_hosts = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "").split(",") if h.strip()]
if allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
```

En desarrollo, sin la variable, no se monta (no molesta al trabajo local).

### 8.2 Documentación oculta

En postura de producción (`REQUIRE_AUTH`), la documentación interactiva y el schema OpenAPI se desactivan para reducir fingerprinting y superficie de ataque:

```python
app = FastAPI(
    ...
    docs_url=None if settings.REQUIRE_AUTH else "/docs",
    redoc_url=None if settings.REQUIRE_AUTH else "/redoc",
    openapi_url=None if settings.REQUIRE_AUTH else "/openapi.json",
)
```

En dev siguen disponibles en `/docs`, `/redoc` y `/openapi.json`.

---

## 9. Control de acceso basado en roles (RBAC)

El modelo de permisos es data-driven: los roles y su matriz de permisos viven en la colección `roles` de Cosmos y se editan desde el módulo Admin. No hay permisos hardcodeados en el flujo de negocio.

### 9.1 Catálogo de permisos

Definido en `app/features/auth/models.py`:

```python
PERMISSIONS = (
    "model.view",      # Ver modelo / Canvas
    "model.edit",      # Crear / editar tablas (working copy)
    "review.decide",   # Aprobar / rechazar solicitudes
    "publish",         # Publicar a producción
    "export",          # Exportar DDL / metadata
    "standards.edit",  # Editar Data Standards (UDP / Parent Domains)
    "admin.manage",    # Administrar usuarios y permisos
)
```

Los permisos efectivos de un usuario se derivan de su rol de forma pura y **filtrando keys desconocidas** (`effective_permissions`): cualquier permiso que el rol no declare queda en `False`, y cualquier key fuera del catálogo se descarta. Un usuario sin rol (o con rol vacío) no tiene ningún permiso.

### 9.2 Dos dependencias de autorización

En `app/features/auth/deps.py`:

- **`require_permission(perm)`** — exige el permiso siempre, sin importar el método. Se usa para endpoints que en su totalidad requieren un permiso (todo el módulo Admin usa `require_permission("admin.manage")`).
- **`write_guard(perm)`** — dependency de router que gatea por método: lecturas (`GET`/`HEAD`/`OPTIONS`) solo exigen sesión válida; escrituras (`POST`/`PUT`/`PATCH`/`DELETE`) exigen el permiso. Se monta con `dependencies=[Depends(write_guard(...))]` para cerrar de una todos los endpoints directos de un router. Esto cerró un hueco: antes había routers cuyos endpoints directos no tenían ninguna dependencia de auth y aceptaban requests anónimos.

```python
def write_guard(perm: str):
    async def _dep(request: Request, principal: Principal = Depends(current_principal)):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        user = await service.resolve_session_user(principal.username)
        if user is None or not user["permissions"].get(perm):
            raise HTTPException(403, f"No tenés permiso para esta acción ({perm}).")
        # Auditoría de la acción (best-effort, salta los guardados ruidosos)
        path = request.url.path
        if not any(path.endswith(sfx) for sfx in _NO_AUDIT):   # /layout, /drawings, /tables
            await audit(user["username"], f"{request.method.lower()} {path}", target_type="api")
        return user
    return _dep
```

En ambos casos, el 401 (sin sesión) lo levanta antes `current_principal`; el 403 (con sesión pero sin permiso, o usuario deshabilitado) lo levanta la dependency. Deshabilitar un usuario en Admin revoca su sesión activa en estos endpoints, porque `resolve_session_user` devuelve `None` para cuentas `disabled`.

```mermaid
flowchart TD
    A[Request a router protegido con write_guard] --> B{Metodo de la request}
    B -->|GET HEAD OPTIONS| C[Solo exige sesion valida via current_principal]
    B -->|POST PUT PATCH DELETE| D[resolve_session_user por username]
    D --> E{Usuario existe y tiene el permiso?}
    E -->|No o deshabilitado| F[403 sin permiso]
    E -->|Si| G[Auditar accion y continuar al endpoint]
```

### 9.3 Nadie se auto-otorga admin

Todo el router de Admin (`app/features/admin/router.py`) exige `admin.manage`:

```python
_admin = require_permission("admin.manage")

@router.put("/roles/{key}")
async def upsert_role(key: str, body: RoleBody, user: dict = Depends(_admin)): ...
```

Como editar la matriz de permisos (`upsert_role`) también requiere `admin.manage`, **un usuario sin admin no puede modificar ningún rol**, y por lo tanto no puede otorgarse a sí mismo `admin.manage`. Además:

- **Sanitización de la matriz:** `sanitize_permissions` coacciona a bool y descarta keys fuera del catálogo, así que no se persisten permisos arbitrarios inventados por el cliente.
- **Invariante "nunca sin administrador":** el guard `_survives_admin` verifica, ante cada cambio de rol, deshabilitación, borrado de usuario, edición de la matriz o borrado de rol, que quede al menos un administrador activo. Si el cambio dejaría el sistema sin ningún admin, se rechaza con 400 (`AdminGuardError`), por ejemplo "Ese cambio dejaría el sistema sin ningún administrador."
- **Sin mass assignment de propietario:** en los reportes guardados, el `owner` se fija server-side desde el principal, no desde el body del cliente.

---

## 10. Endurecimiento del motor de reporting frente a inyección

El motor de reporting traduce consultas del cliente (`QuerySpec` o SQL de texto) a agregaciones de Mongo sobre colecciones de gran volumen (cientos de miles de documentos). Como el cliente influye en lo que llega al `$match`, se endurecieron los puntos donde un valor podía convertirse en un operador de Mongo o en una expresión regular maliciosa.

### 10.1 Postura de base (ya sólida)

- El cliente **nunca** manda paths de Mongo: cada `field` pasa por el allowlist del Field Catalog.
- Las operaciones son un enum cerrado, forzado doblemente (Pydantic `Literal` + `OPS_BY_TYPE`).
- El `QuerySpec` usa `extra="forbid"` (rechaza campos desconocidos).
- El parser de SQL es un allowlist sobre sqlglot: rechaza `JOIN`, subqueries y DDL.
- Los operadores `contains`/`startsWith` ya usaban `re.escape`.

### 10.2 `/facets`: `re.escape` + cap de longitud

El endpoint `/api/reporting/facets` alimenta el typeahead de filtros y no requiere autenticación (como el resto del reporting de solo lectura). Un `q` sin escapar iría directo a un `$regex`, habilitando ReDoS o inyección de regex. La corrección (`app/features/reporting/query/router.py`):

```python
async def facets(field: str, from_: str = Query(default="columns", alias="from"),
                 q: str | None = Query(default=None, max_length=80),
                 limit: int = Query(default=50, ge=1, le=200)):
    ...
    if q:
        query["name"] = {"$regex": re.escape(q), "$options": "i"}  # escapado: sin ReDoS/inyección
    ...
    if q:
        match[fd.path] = {"$regex": re.escape(q), "$options": "i"}  # escapado: sin ReDoS/inyección
```

`q` se limita a 80 caracteres y se escapa siempre. Verificado en vivo: un patrón malicioso como `((a+)+)+$` responde en 0.36 s (escapado, tratado como texto literal) en lugar de colgar el proceso.

### 10.3 Cursor keyset validado contra inyección de operadores NoSQL

La paginación keyset codifica el cursor como base64 de un JSON que el cliente devuelve y que va **directo al `$match`**. Si el valor de orden fuese un dict o una lista, un atacante inyectaría operadores de Mongo: `{"$ne": null}` bypassearía el keyset, `{"$regex": "(a+)+$"}` provocaría ReDoS. La validación (`app/features/reporting/query/executor.py`):

```python
def _decode_cursor(cur: str):
    try:
        v = json.loads(base64.urlsafe_b64decode(cur.encode()).decode())
    except Exception:
        raise QueryError("Cursor inválido", code=400)
    # Solo escalares para el valor y string para el _id
    if (not isinstance(v, list) or len(v) != 2
            or isinstance(v[0], (dict, list)) or not isinstance(v[1], str)):
        raise QueryError("Cursor inválido", code=400)
    return v
```

Cubierto por `tests/features/reporting/test_query_security.py`, que verifica explícitamente el rechazo de `[{"$ne": None}, "id"]` y `[{"$regex": "(a+)+$"}, "id"]`, además de cursores malformados.

### 10.4 `re.escape` del abbrev del glosario (ReDoS de segundo orden)

En `glossary_usage` (`app/features/reporting/views.py`), la abreviatura del glosario proviene de datos almacenados y se usa en un `$regex` para contar coincidencias en `physicalName`. Sin escapar, un abbrev con metacaracteres sería un ReDoS de segundo orden (el payload malicioso entra por otra ruta y detona al reportar):

```python
{**ACTIVE, "physicalName": {"$regex": f"(^|_){re.escape(ab)}(_|$)"}}
```

### 10.5 Spec guardado validado como `QuerySpec`

Al persistir un reporte guardado, el spec se valida como `QuerySpec` antes de escribirlo, para no almacenar blobs arbitrarios (defensa en profundidad, aunque `/query` re-valide al ejecutar):

```python
def _validate_report_spec(spec: dict) -> None:
    try:
        QuerySpec.model_validate(spec)
    except ValidationError as e:
        raise HTTPException(422, "El spec del reporte no es una consulta válida.") from e
```

**Nota de superficie:** las lecturas de reporting (`/query`, `/facets`, `/export`, `/insights`) están abiertas por diseño; en producción el login global de `current_principal` las gatea cuando `REQUIRE_AUTH=true`. Reforzar estas rutas con `current_principal` explícito está listado en pendientes para reducir la superficie de DoS.

---

## 11. Auditoría

La auditoría (`app/core/audit.py`) es un log append-only en la colección `audit_log`, con la firma `{at, actor, action, target?, targetType?, meta?}`. Es **best-effort**: nunca hace fallar la request que la invoca, porque un fallo al escribir el log no debe tumbar una operación de negocio.

```python
async def audit(actor, action, *, target=None, target_type=None, meta=None) -> None:
    entry = {"at": datetime.now(timezone.utc).isoformat(), "actor": actor, "action": action}
    ...
    try:
        db = await get_db()
        await db["audit_log"].insert_one(entry)
    except Exception:  # la auditoría nunca rompe el flujo
        log.warning("audit write failed", extra={"action": action, "actor": actor})
```

Qué se audita:

- **Eventos de sesión:** `login`, `login_failed`, `login_locked`, `logout`.
- **Acciones de escritura** vía `write_guard`: `POST/PUT/PATCH/DELETE <ruta>` con actor. Se saltan los guardados de alta frecuencia del canvas (`/layout`, `/drawings`, `/tables`) para no inundar el log.
- **Operaciones de Admin:** creación, edición y borrado de usuarios y roles.

La lectura del log está protegida: `GET /api/admin/audit` exige `admin.manage`. Además de auditoría, todas las requests llevan un `X-Request-ID` correlacionado en los logs estructurados de entrada y salida, útil para el diagnóstico ("revisá los logs con el X-Request-ID" es lo que devuelve el envelope de error 500).

---

## 12. Tabla resumen de mitigaciones implementadas

| # | Mitigación | Archivos clave | Gateo |
|---|---|---|---|
| 1 | Hash bcrypt en tiempo constante + clip determinista a 72 bytes | `core/security.py` | Siempre |
| 2 | Dummy hash anti-enumeración por temporización | `features/auth/service.py` | Siempre |
| 3 | JWT HS256 con `algorithms` fijado y `exp` obligatorio | `core/security.py` | Siempre |
| 4 | `assert_secure_config` falla-cerrado con SECRET_KEY default | `core/config.py`, `main.py` | `REQUIRE_AUTH` |
| 5 | Identidad token-first; sin token y `REQUIRE_AUTH` → 401 | `core/identity/dependencies.py` | `REQUIRE_AUTH` |
| 6 | Rate limiting del login `5/minute` por IP → 429 | `core/ratelimit.py`, `features/auth/router.py`, `main.py` | `REQUIRE_AUTH` o `RATE_LIMIT_ENABLED` |
| 7 | Lockout por intentos fallidos (8 fallos → 15 min, `$inc` atómico) | `features/auth/service.py`, `repository.py` | Siempre |
| 8 | Política de contraseñas (min 10 / max 128) al crear o cambiar | `features/admin/schemas.py` | Siempre |
| 9 | Cabeceras de seguridad (nosniff, DENY, Referrer, Permissions, HSTS) | `main.py` | HSTS solo HTTPS |
| 10 | CORS estricto (`allow_credentials=False`, sin comodines) | `main.py` | Siempre |
| 11 | TrustedHost (host allowlist) | `main.py` | `ALLOWED_HOSTS` |
| 12 | `/docs`, `/redoc`, `/openapi.json` ocultos en producción | `main.py` | `REQUIRE_AUTH` |
| 13 | RBAC: `require_permission` + `write_guard` (gateo por método) | `features/auth/deps.py` | Siempre |
| 14 | Nadie se auto-otorga admin + invariante "nunca sin administrador" | `features/admin/router.py`, `service.py` | Siempre |
| 15 | `/facets` con `re.escape` + cap de longitud (ReDoS/regex-injection) | `features/reporting/query/router.py` | Siempre |
| 16 | Validación del cursor keyset (anti operator-injection NoSQL) | `features/reporting/query/executor.py` | Siempre |
| 17 | `re.escape` del abbrev del glosario (ReDoS de 2° orden) | `features/reporting/views.py` | Siempre |
| 18 | Spec guardado validado como `QuerySpec` | `features/reporting/query/router.py` | Siempre |
| 19 | Envelope de error genérico (sin stack trace al cliente) | `main.py` | Siempre |
| 20 | Auditoría append-only best-effort | `core/audit.py` | Siempre |

---

## 13. Estado PENDIENTE (fase siguiente)

Los siguientes puntos están identificados pero **no** implementados todavía:

| Pendiente | Severidad | Descripción |
|---|---|---|
| **Revocación de JWT** | Alta | El TTL de 12 h sin denylist implica que deshabilitar un usuario no corta su sesión hasta 12 h en endpoints que solo dependen de `current_principal` (los que usan `resolve_session_user`, como Admin y `write_guard`, sí lo cortan de inmediato). Opciones: bajar el TTL a 15-30 min con refresh revocable, agregar `tokenVersion` por usuario, o revalidar `status != disabled` contra la DB en cada request. |
| **Rate limiting con Redis** | Media | El estado del limiter es en memoria por proceso. Para multi-réplica (Databricks Apps con varias instancias) migrar a un backend Redis (`Limiter(storage_uri="redis://…")` o `fastapi-limiter`); si no, cada réplica cuenta por separado y el límite efectivo se multiplica por el número de réplicas. |
| **Contraseñas filtradas** | Media | Sumar verificación contra brechas conocidas (HIBP con k-anonymity) o fuerza (zxcvbn) además del mínimo de longitud. |
| **Claims `iss` / `aud` en el JWT** | Baja | Agregar emisor y audiencia acota el uso del token a este servicio. |
| **Migrar bcrypt → argon2id** | Baja | Elimina el clip a 72 bytes y moderniza el algoritmo de derivación. |
| **Auth explícita en lecturas de reporting** | Media | `/query`, `/facets`, `/export` e `/insights` son abiertas; gatearlas con `current_principal` reduce la superficie de DoS sobre las colecciones de gran volumen (hoy quedan protegidas solo por el login global cuando `REQUIRE_AUTH=true`). |

---

## 14. Despliegue: dónde corre, variables y base de datos

### 14.1 Dónde corre

El backend es una app FastAPI (ASGI) pensada para correr detrás de un proxy TLS, en dos entornos objetivo:

- **Azure App Service.** El proxy termina TLS y reenvía `X-Forwarded-Proto`, que el middleware usa para decidir si emitir HSTS.
- **Databricks Apps.** El proxy SSO reenvía cabeceras de identidad; si en el futuro se reactivara el modo `databricks`, el seam de identidad las leería (`X-Forwarded-Email`, `X-Forwarded-Preferred-Username`, `X-Forwarded-User`). Hoy la autenticación real es la propia por token.

En ambos casos, la postura de producción se activa con `REQUIRE_AUTH=true`, lo que además exige un `SECRET_KEY` propio (si no, la app no arranca). El proceso se sirve con Uvicorn/Gunicorn; el `if __name__ == "__main__"` de `main.py` es solo para desarrollo local.

### 14.2 Base de datos

La persistencia es **Azure Cosmos DB con la API de MongoDB**, accedida vía Motor (async) con la cadena de conexión en `COSMOS_CONNECTION_STRING`. Consideraciones:

- **Cadena de conexión como secreto.** Debe ir en un secreto gestionado (Azure Key Vault o el store de secretos de Databricks Apps), nunca en el repositorio ni en el `.env` versionado.
- **Colecciones de la app:** `users`, `roles`, `audit_log`, más las de negocio (`projects`, `canonical_tables`, `canonical_columns`, `relationships`, `views`, `parent_domains`, `udp_definitions`, reportes guardados, etc.).
- **Índices a escala.** El motor de reporting depende de índices sobre los campos de filtro y orden (por ejemplo `schema`, `physicalName`, `flgactive`) para la paginación keyset y las facetas; sin ellos, las consultas sobre colecciones de cientos de miles de documentos degradan. El circuit-breaker `maxTimeMS = 15000` corta consultas que se pasen de 15 s.
- **Borrado lógico.** Los documentos usan `flgactive`; los filtros incluyen `{"flgactive": {"$ne": False}}` para excluir los borrados. La conexión se abre y cierra en el `lifespan` de FastAPI.

### 14.3 Variables de entorno

| Variable | Propósito | Producción |
|---|---|---|
| `COSMOS_CONNECTION_STRING` | Cadena de conexión a Cosmos (secreto). | Obligatoria |
| `COSMOS_DATABASE` | Nombre de la base (default `db_modeler`). | Recomendada |
| `SECRET_KEY` | Clave HMAC para firmar el JWT de sesión. **La app no arranca con el default si `REQUIRE_AUTH=true`.** | Obligatoria |
| `REQUIRE_AUTH` | `true` activa la postura de producción (login obligatorio, docs ocultos, rate limit, falla-cerrado). | `true` |
| `ACCESS_TOKEN_TTL_MIN` | Vida del token en minutos (default 720). Bajarlo mitiga la falta de revocación. | Opcional |
| `CORS_ORIGINS` | Lista de orígenes permitidos, separada por comas. | Obligatoria |
| `ALLOWED_HOSTS` | Lista de hosts permitidos para `TrustedHostMiddleware`. | Recomendada |
| `RATE_LIMIT_ENABLED` | Fuerza el rate limiting aun sin `REQUIRE_AUTH`. | Redundante si `REQUIRE_AUTH=true` |
| `AUTH_MODE` | `local` o `databricks` (solo afecta el fallback del seam; la auth real es por token). | `local` |
| `LOCAL_DEV_USER` / `LOCAL_DEV_USERNAME` / `LOCAL_DEV_DISPLAY_NAME` | Usuario fake del seam en desarrollo. | No usar en prod |

Ejemplo de configuración de producción:

```bash
REQUIRE_AUTH=true
SECRET_KEY=<valor-aleatorio-largo-desde-el-secret-store>
COSMOS_CONNECTION_STRING=<secreto>
COSMOS_DATABASE=db_modeler
CORS_ORIGINS=https://data-model-hub.example
ALLOWED_HOSTS=data-model-hub-api.example
ACCESS_TOKEN_TTL_MIN=30
```

---

## 15. Checklist de verificación antes de desplegar

- [ ] `SECRET_KEY` definido con un valor aleatorio largo (no el default). Con `REQUIRE_AUTH=true` la app se niega a arrancar si no.
- [ ] `REQUIRE_AUTH=true` para activar login obligatorio, docs ocultos y rate limiting.
- [ ] `CORS_ORIGINS` apuntando solo al frontend real (sin comodines).
- [ ] `ALLOWED_HOSTS` con los hosts públicos del servicio.
- [ ] `COSMOS_CONNECTION_STRING` desde un secret store, nunca en el repositorio.
- [ ] TLS terminado en el proxy y `X-Forwarded-Proto` reenviado (para HSTS).
- [ ] Índices de Cosmos creados sobre los campos de filtro y orden del reporting.
- [ ] Al menos un usuario con rol que incluya `admin.manage` (el invariante impide quedarse sin administrador, pero hay que crear el primero).

Verificado en vivo durante el hardening: las cabeceras de seguridad aparecen en toda respuesta; el sexto intento de login dentro del minuto devuelve 429; un regex malicioso en `/facets` responde escapado en 0.36 s sin ReDoS; y la suite de tests (209 casos) cubre el lockout de login y la inyección del cursor keyset.
