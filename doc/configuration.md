# Configuración

> Toda la configuración del backend de plataforma se carga desde un único
> archivo `.env` en la raíz del repo. El módulo `app/core/config.py` expone el
> singleton `settings`. **No hay configuración por entorno dentro del repo** —
> cada deploy inyecta su propio `.env` (o, en Databricks Apps, variables de la app
> y un secret para la connection string).

> Las variables de LLM, Foundry, embeddings, rate limiters y engines viven en el
> servicio separado `app-agents-modeler`. Acá solo hay Cosmos DB, CORS y logging.
> **MVP sin auth**: no hay `AUTH_SECRET` ni secretos de sesión.

## 1. Variables de entorno

### 1.1 Azure Cosmos DB for MongoDB

| Variable | Descripción | Default | Requerida |
|---|---|---|---|
| `COSMOS_CONNECTION_STRING` | Connection string completo (`mongodb+srv://...`) | `""` | ✓ |
| `COSMOS_DATABASE` | Nombre de la base de datos | `db_modeler` | — |

Cuando `COSMOS_CONNECTION_STRING` está vacío, la conexión Motor falla en el
lifespan. El proceso sigue arriba (para que `/api/health` reporte el estado), pero
los endpoints que toquen DB devuelven 500. Útil para diagnosticar credenciales
sin matar el contenedor.

> En **Databricks Apps**, la connection string se inyecta como un **secret** de
> la app (`valueFrom`), no en texto plano. Ver `../DEPLOY-databricks.md` §3.

### 1.2 CORS

| Variable | Descripción | Default |
|---|---|---|
| `CORS_ORIGINS` | Lista separada por comas de orígenes permitidos | `http://localhost:3000,http://127.0.0.1:3000` |

En producción (Databricks Apps) apuntá `CORS_ORIGINS` a la URL de la app frontend.
`allow_credentials=True` está fijo en código (no-op en el MVP sin cookies).

### 1.3 Logging

| Variable | Descripción | Default |
|---|---|---|
| `LOG_FORMAT` | `pretty` (humano, colores) o `json` (un objeto por línea, ideal para colectores) | `pretty` |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` | `INFO` |

Cada request se loguea inicio/fin con `request_id` y duración en ms; el mismo
`request_id` se devuelve en el header `X-Request-ID`.

---

## 2. Archivo `.env.example`

```env
# ─── Azure Cosmos DB for MongoDB (vCore) ───────────────────────
COSMOS_CONNECTION_STRING=mongodb+srv://<user>:<password>@<cluster>.global.mongocluster.cosmos.azure.com/?tls=true&authMechanism=SCRAM-SHA-256&retrywrites=false&maxIdleTimeMS=120000
COSMOS_DATABASE=db_modeler

# ─── CORS ──────────────────────────────────────────────────────
# CORS_ORIGINS=http://localhost:3000,https://modeler.example.com

# ─── Logging ───────────────────────────────────────────────────
LOG_FORMAT=pretty
LOG_LEVEL=INFO
```

---

## 3. Acceso desde código

```python
from app.core.config import settings

settings.COSMOS_CONNECTION_STRING   # "mongodb+srv://..."
settings.COSMOS_DATABASE            # "db_modeler"
settings.PROJECT_ROOT               # absolute Path al repo
```

`Settings` es una clase Python plana (sin `pydantic-settings`) porque solo expone
un par de valores — agregar Pydantic acá sería gold-plating.

---

## 4. Excel-import: parámetros internos

El módulo `app/features/excel_import/` tiene constantes calibrables que **no se
exponen como env vars** (son parte de la lógica del feature):

| Constante | Archivo | Default | Para qué |
|---|---|---|---|
| `_MAX_UPLOAD_BYTES` | `app/features/excel_import/router.py` | `10 * 1024 * 1024` (10 MB) | Tope de tamaño del upload |
| `_ALLOWED_EXTENSIONS` | `app/features/excel_import/router.py` | `(".xlsx", ".xlsm")` | Extensiones aceptadas |
| `FUZZY_THRESHOLD` | `app/features/excel_import/normalizer.py` | `75.0` | Umbral mínimo (0-100) para un match fuzzy |
| `TABLE_DESCRIPTIONS_SHEET` | `app/features/excel_import/reader.py` | `"tablesdescriptions"` | Nombre (case-insensitive) de la hoja opcional |
| `CANONICAL_TYPES` | `app/features/excel_import/normalizer.py` | ~30 tipos | Universo de tipos canónicos |
| `ALIASES` | `app/features/excel_import/normalizer.py` | dict pequeño | Sinónimos no ambiguos (`int → integer`) |

> Subir `FUZZY_THRESHOLD` reduce falsos positivos pero rechaza más typos reales.
> El valor actual (75) deja entrar `decximam → decimal` (≈86) pero no `foobar`.

> `CANONICAL_TYPES` debe quedar alineado con `COLUMN_DATA_TYPES` en
> `web-data-model-hub/src/types/model.ts`.

---

## 5. Dependencias

`requirements.txt`:

```
# Pydantic + env
pydantic>=2.0
python-dotenv

# API Server
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
python-multipart>=0.0.9

# Cosmos DB (Motor async + pymongo for type compat)
pymongo>=4.0
motor>=3.3.0

# Excel import
openpyxl>=3.1
rapidfuzz>=3.6
```

> Auth (`PyJWT` + `bcrypt`) se removió con el MVP sin permisos.

### Instalación + verificación

```bash
/opt/homebrew/bin/python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Importa todo el backend sin arrancar — debe terminar en 0
.venv/bin/python -c "import app.main; print('ok')"
curl http://localhost:8000/api/health
```

---

## 6. Despliegue

A **Databricks Apps** (no App Service / Docker). El comando de arranque y la
inyección de secretos van en `app.yaml`; el recurso de la app en `databricks.yml`.
Guía completa en [`../DEPLOY-databricks.md`](../DEPLOY-databricks.md).
