# Configuración

> Toda la configuración del backend de plataforma se carga desde un
> único archivo `.env` en la raíz del repo. El módulo `src/config.py`
> expone el singleton `settings`. **No hay configuración por entorno
> dentro del repo** — cada deploy inyecta su propio `.env`.

> Las variables relacionadas con LLM, Foundry, embeddings, rate limiters
> y engines viven en el servicio separado `app-agents-modeler`. Acá solo
> hay Cosmos DB, JWT, CORS y logging.

## 1. Variables de entorno

### 1.1 Azure Cosmos DB for MongoDB

| Variable | Descripción | Default | Requerida |
|---|---|---|---|
| `COSMOS_CONNECTION_STRING` | Connection string completo (`mongodb+srv://...`) | `""` | ✓ |
| `COSMOS_DATABASE` | Nombre de la base de datos | `db_modeler` | — |

Cuando `COSMOS_CONNECTION_STRING` está vacío, `motor_client.connect()`
lanza `RuntimeError` en el lifespan. El proceso sigue arriba (para que
`/api/health` reporte el estado), pero los endpoints que toquen DB
devuelven 500. Útil para diagnosticar errores de credenciales sin
matar el contenedor.

> El backend comparte la **misma cuenta de Cosmos** con el servicio de
> agentes (`app-agents-modeler`). Cada uno toca colecciones disjuntas
> (este: `users` / `projects` / `models` / `model_tables` /
> `model_relationships` / `model_views`; el agente: `column_catalog`).

### 1.2 Autenticación

| Variable | Descripción | Default |
|---|---|---|
| `AUTH_SECRET` | Secret HS256 compartido con el frontend Next.js. **Debe coincidir literalmente** entre `web-data-model-hub/.env` y este `.env`. | dev fallback (no usar en prod) |

Generar uno nuevo:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

> ⚠️ Si los dos `.env` divergen, el browser firma el JWT con un secret
> y el backend lo valida con otro → 401 silencioso en cada request.
> Esta es la causa más frecuente de "el login se queda colgado".

Otras constantes hardcodeadas en `src/api/auth.py` (no se exponen como
env vars porque están atadas a la implementación):

| Constante | Valor | Razón |
|---|---|---|
| `TOKEN_LIFETIME_SECONDS` | `10 * 60 * 60` (10 h) | jornada laboral típica |
| `JWT_ALG` | `HS256` | match con el `jose` del Next.js middleware |
| `AUTH_COOKIE_NAME` | `modeler-auth` | compartido con el frontend |
| `_BCRYPT_ROUNDS` | `10` | balance latencia/seguridad |

### 1.3 CORS

| Variable | Descripción | Default |
|---|---|---|
| `CORS_ORIGINS` | Lista separada por comas de orígenes permitidos | `http://localhost:3000,http://127.0.0.1:3000` |

`allow_credentials=True` está fijo en código — sin él, el cookie
`modeler-auth` no cruza el origen y el frontend pierde la sesión.

### 1.4 Logging

| Variable | Descripción | Default |
|---|---|---|
| `LOG_FORMAT` | `pretty` (humano, colores) o `json` (un objeto por línea, ideal para colectores) | `pretty` |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` | `INFO` |

Cada request se loguea inicio/fin con `request_id` y duración en ms; el
mismo `request_id` se envía de regreso en el header `X-Request-ID`.

---

## 2. Archivo `.env.example`

Versión vigente (verbatim — copia de `.env.example`):

```env
# ─── Azure Cosmos DB for MongoDB (vCore) ───────────────────────
# Misma cuenta que el servicio de agentes (`app-agents-modeler`).
# Cada uno toca colecciones distintas (ver src/db/__init__.py).
COSMOS_CONNECTION_STRING=mongodb+srv://<user>:<password>@<cluster>.global.mongocluster.cosmos.azure.com/?tls=true&authMechanism=SCRAM-SHA-256&retrywrites=false&maxIdleTimeMS=120000
COSMOS_DATABASE=db_modeler

# ─── Autenticación JWT ─────────────────────────────────────────
# Secreto HS256 para firmar tokens de sesión. Genera uno con:
#   python -c "import secrets; print(secrets.token_hex(32))"
# DEBE coincidir con el AUTH_SECRET del frontend (web-data-model-hub/.env).
AUTH_SECRET=your-auth-secret-here

# ─── CORS ──────────────────────────────────────────────────────
# Orígenes permitidos por CORS, separados por coma.
# Default: localhost:3000 y 127.0.0.1:3000 (Next.js dev).
# Ejemplo:
# CORS_ORIGINS=http://localhost:3000,https://modeler.example.com

# ─── Logging ───────────────────────────────────────────────────
# Formato de logs: "pretty" (legible para dev) o "json" (producción).
LOG_FORMAT=pretty
# Nivel mínimo: DEBUG, INFO, WARNING, ERROR, CRITICAL.
LOG_LEVEL=INFO
```

---

## 3. Acceso desde código

```python
from src.config import settings

settings.COSMOS_CONNECTION_STRING   # "mongodb+srv://..."
settings.COSMOS_DATABASE            # "db_modeler"
settings.PROJECT_ROOT               # absolute Path al repo
```

`Settings` es una clase Python plana (sin `pydantic-settings`) porque
solo expone dos valores — agregar Pydantic acá sería gold-plating.

`AUTH_SECRET` se lee con `os.getenv` directamente en `src/api/auth.py`,
no se expone en `settings` para evitar leakage accidental al loguear
todo el objeto de settings en debug.

---

## 4. Excel-import: parámetros internos

El módulo `src/excel_import/` también tiene constantes calibrables, pero
**no se exponen como env vars** — son parte de la lógica del feature.
Si necesitas tocarlas, edita el módulo:

| Constante | Archivo | Default | Para qué |
|---|---|---|---|
| `_MAX_UPLOAD_BYTES` | `api/routes/excel_import.py` | `10 * 1024 * 1024` (10 MB) | Tope defensivo de tamaño del upload |
| `_ALLOWED_EXTENSIONS` | `api/routes/excel_import.py` | `(".xlsx", ".xlsm")` | Extensiones aceptadas |
| `FUZZY_THRESHOLD` | `src/excel_import/type_normalizer.py` | `75.0` | Umbral mínimo (0-100) para aceptar un match fuzzy |
| `TABLE_DESCRIPTIONS_SHEET` | `src/excel_import/workbook_reader.py` | `"tablesdescriptions"` | Match case-insensitive del nombre de la hoja opcional |
| `CANONICAL_TYPES` | `src/excel_import/type_normalizer.py` | tupla con ~30 tipos | Universo de tipos canónicos (match exacto) |
| `ALIASES` | `src/excel_import/type_normalizer.py` | dict pequeño | Sinónimos NO ambiguos (`int → integer`, `bool → boolean`) |

> Subir `FUZZY_THRESHOLD` reduce falsos positivos pero rechaza más typos
> reales. Bajarlo invita matches espurios (`datatime` → `time`).
> El valor actual (75) está calibrado para que `decximam → decimal`
> entre (≈86) pero `foobar` no.

> `CANONICAL_TYPES` debe quedar alineado con `COLUMN_DATA_TYPES` en
> `web-data-model-hub/src/types/model.ts`. Cuando agregues un tipo en
> uno, agrégalo también en el otro.

---

## 5. Dependencias

`requirements.txt` (verbatim):

```
# Pydantic + env
pydantic>=2.0
python-dotenv

# API Server
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
python-multipart>=0.0.9   # required by FastAPI for multipart uploads

# Cosmos DB (Motor async + pymongo for type compat)
pymongo>=4.0
motor>=3.3.0

# Authentication (JWT + bcrypt)
PyJWT>=2.0
bcrypt>=4.0

# Excel import
openpyxl>=3.1
rapidfuzz>=3.6
```

### Instalación

```bash
# Crear venv local (Python 3.12)
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Verificación rápida

```bash
# Importa todo el backend sin arrancar el servidor — debe terminar en 0
.venv/bin/python -c "from api.main import app; print('ok')"

# Probar el endpoint de health
curl http://localhost:8000/api/health
# → {"status":"ok","version":"1.0.0","db_connected":true}
```

---

## 6. Despliegue

`Dockerfile` es multi-stage (Python 3.12-slim). Puerto expuesto: `8000`.
Para Azure App Service:

| Variable de plataforma | Valor |
|---|---|
| `WEBSITES_PORT` | `8000` |
| `WEBSITES_CONTAINER_START_TIME_LIMIT` | `300` (Cosmos puede tardar en responder durante el cold start) |

Y todas las variables descritas arriba como App Settings (no hardcodear
en la imagen).
