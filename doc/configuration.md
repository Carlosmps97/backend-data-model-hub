# Configuración

## Visión general

La configuración del sistema se gestiona a través de un archivo `.env` en la raíz del proyecto. El módulo `src/config.py` carga estas variables al iniciar y las expone como un singleton `settings` accesible desde cualquier módulo.

---

## Variables de entorno

### Azure AI Foundry

| Variable | Descripción | Default | Requerida |
|----------|-------------|---------|-----------|
| `FOUNDRY_PROJECT_ENDPOINT` | URL del proyecto en Azure AI Foundry | — | ✓ |
| `FOUNDRY_MODEL` | Modelo LLM a utilizar | `gpt-4o` | ✗ |
| `FOUNDRY_API_KEY` | API Key (alternativa a Service Principal) | `""` | ✗ |

### Azure Service Principal

| Variable | Descripción | Default | Requerida |
|----------|-------------|---------|-----------|
| `APP_AZURE_TENANT_ID` | ID del tenant de Azure AD | `""` | ✓ |
| `APP_AZURE_CLIENT_ID` | ID de la aplicación registrada | `""` | ✓ |
| `APP_AZURE_CLIENT_SECRET` | Secret de la aplicación | `""` | ✓ |

### Azure OpenAI (alternativo)

| Variable | Descripción | Default | Requerida |
|----------|-------------|---------|-----------|
| `AZURE_OPENAI_ENDPOINT` | Endpoint de Azure OpenAI | `""` | ✗ |
| `AZURE_OPENAI_API_VERSION` | Versión de API | `2025-01-01-preview` | ✗ |

### Rutas de datos

| Variable | Descripción | Default |
|----------|-------------|---------|
| `GUIDELINES_PATH` | Ruta relativa al archivo de lineamientos | `data/guidelines/modeling_guidelines.json` |
| `COLUMN_CATALOG_PATH` | Ruta relativa al catálogo de columnas | `data/column_catalog.json` |

### Motor de BD

| Variable | Descripción | Default |
|----------|-------------|---------|
| `DEFAULT_DB_ENGINE` | Motor de BD por defecto | `databricks_sql` |

Motores válidos: `databricks_sql`, `cosmosdb`, `sqlserver`, `postgresql`, `mysql`.

### API REST

Aplican solo cuando se ejecuta la API (`uvicorn api.main:app`). El CLI las ignora.

| Variable | Descripción | Default |
|----------|-------------|---------|
| `MAX_CONCURRENT_PIPELINES` | Máximo de pipelines `POST .../model` ejecutándose en paralelo. Implementado como `asyncio.Semaphore` que envuelve únicamente al endpoint del modelo (no afecta health, engines, GET del modelo). | `5` |
| `CORS_ORIGINS` | Lista separada por comas de orígenes permitidos por CORS. | `http://localhost:3000,http://127.0.0.1:3000` |

### Logging

| Variable | Descripción | Default |
|----------|-------------|---------|
| `LOG_FORMAT` | `pretty` (legible) o `json` (un objeto por línea). | `pretty` |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. | `INFO` |

---

## Archivo `.env.example`

```env
# Azure AI Foundry
FOUNDRY_PROJECT_ENDPOINT=https://<tu-recurso>.services.ai.azure.com/api/projects/<tu-proyecto>
FOUNDRY_MODEL=gpt-4o
FOUNDRY_API_KEY=

# Azure Service Principal
APP_AZURE_TENANT_ID=<tu-tenant-id>
APP_AZURE_CLIENT_ID=<tu-client-id>
APP_AZURE_CLIENT_SECRET=<tu-client-secret>

# Azure OpenAI (alternativo)
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_API_VERSION=2025-01-01-preview

# Rutas de datos
GUIDELINES_PATH=data/guidelines/modeling_guidelines.json
COLUMN_CATALOG_PATH=data/column_catalog.json

# Motor de BD por defecto
DEFAULT_DB_ENGINE=databricks_sql

# API REST
MAX_CONCURRENT_PIPELINES=5
# CORS_ORIGINS=http://localhost:3000

# Logging
LOG_FORMAT=pretty
LOG_LEVEL=INFO
```

---

## Configurar credenciales de Azure

### Paso 1: Registrar aplicación en Azure AD

1. Ir a **Azure Portal** → **Azure Active Directory** → **App registrations**.
2. Crear nueva aplicación.
3. Copiar **Application (client) ID** y **Directory (tenant) ID**.
4. En **Certificates & secrets** → crear nuevo client secret.
5. Copiar el valor del secret.

### Paso 2: Asignar permisos

La aplicación registrada necesita permisos para:
- **Azure AI Foundry**: rol `Cognitive Services User` o `Cognitive Services Contributor` en el recurso AI.
- **Azure OpenAI** (si se usa como alternativa): mismo rol.

### Paso 3: Configurar `.env`

```env
APP_AZURE_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
APP_AZURE_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
APP_AZURE_CLIENT_SECRET=tu-secret-aqui
FOUNDRY_PROJECT_ENDPOINT=https://tu-recurso.services.ai.azure.com/api/projects/tu-proyecto
```

---

## Clase Settings

El singleton `Settings` (`src/config.py`) centraliza toda la configuración:

```python
from src.config import settings

# Acceder a configuración
print(settings.FOUNDRY_MODEL)         # "gpt-4o"
print(settings.DEFAULT_DB_ENGINE)     # "databricks_sql"
print(settings.GUIDELINES_PATH)       # Path absoluto al archivo
print(settings.SUPPORTED_ENGINES)     # ["databricks_sql", "cosmosdb", ...]
print(settings.PROJECT_ROOT)          # Path raíz del proyecto
```

### Resolución de rutas
Las rutas `GUIDELINES_PATH` y `COLUMN_CATALOG_PATH` se resuelven como **rutas absolutas** relativas a la raíz del proyecto (`_PROJECT_ROOT`), independientemente de desde dónde se ejecute el script.

---

## Lineamientos de modelamiento

### Estructura del archivo (`modeling_guidelines.json`)

```json
{
  "version": "1.0",
  "area": "default",
  "naming_conventions": {
    "table_prefix": "tbl_",
    "column_case": "snake_case",
    "column_prefixes": {
      "identifier": "id_",
      "name": "name_",
      "date": "date_",
      "amount": "amount_",
      "flag": "flag_",
      "..."
    },
    "reserved_words_to_avoid": ["user", "table", "order", "..."],
    "max_column_name_length": 64,
    "max_table_name_length": 64
  },
  "audit_columns": {
    "required": true,
    "columns": [
      {"column_name": "date_created", "data_type": "TIMESTAMP", "..."},
      {"column_name": "date_updated", "..."},
      {"column_name": "user_created", "..."},
      {"column_name": "user_updated", "..."}
    ]
  },
  "primary_key_conventions": {
    "naming_pattern": "id_{entity}",
    "preferred_type": "BIGINT"
  },
  "foreign_key_conventions": {
    "naming_pattern": "id_{referenced_entity}",
    "suffix_constraint": "_fk"
  },
  "data_type_mappings": {
    "databricks_sql": {"VARCHAR": "STRING", "BOOLEAN": "BOOLEAN", "..."},
    "sqlserver": {"VARCHAR": "NVARCHAR", "BOOLEAN": "BIT", "..."},
    "postgresql": {"VARCHAR": "VARCHAR", "BOOLEAN": "BOOLEAN", "..."},
    "mysql": {"VARCHAR": "VARCHAR", "BOOLEAN": "TINYINT(1)", "..."},
    "cosmosdb": {"VARCHAR": "string", "BOOLEAN": "boolean", "..."}
  },
  "general_rules": [
    "Todas las tablas deben tener columnas de auditoría",
    "Los nombres deben estar en snake_case y en inglés",
    "..."
  ]
}
```

### Personalización
Para personalizar los lineamientos:
1. Editar `data/guidelines/modeling_guidelines.json`.
2. O crear un archivo en otro formato soportado (XLSX, DOCX, PDF, TXT).
3. Actualizar `GUIDELINES_PATH` en `.env`.

---

## Catálogo de columnas

### Estructura del archivo (`column_catalog.json`)

```json
{
  "columns": [
    {
      "column_name": "id_customer",
      "functional_definition": "Identificador único del cliente",
      "data_type": "BIGINT",
      "used_in_tables": ["tbl_customers", "tbl_orders"],
      "is_new": false
    }
  ]
}
```

### Comportamiento en runtime
- **Lectura**: el QAValidatorAgent consulta el catálogo para estandarizar nombres.
- **Escritura**: columnas nuevas se agregan automáticamente con `is_new: true`.
- **Thread safety**: operaciones protegidas con `threading.Lock`.
- **Persistencia**: cambios se guardan inmediatamente en el archivo JSON.

---

## Dependencias

### Archivo `requirements.txt`

```
agent-framework>=0.1
openpyxl>=3.1
python-docx>=1.0
pdfplumber>=0.10
pydantic>=2.0
rich>=13.0
python-dotenv>=1.0
azure-identity>=1.15
```

### Instalación

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
