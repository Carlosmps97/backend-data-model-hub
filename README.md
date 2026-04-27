# Data Modeler Agent — Backend

Sistema multi-agente experto en modelamiento de datos construido con el **Microsoft Agent Framework** (Python 3.12). Genera modelos de datos profesionales, DDL ejecutable y reportes de calidad a partir de definiciones funcionales en lenguaje natural o archivos Excel.

> **Stack**: Microsoft Agent Framework · Azure AI Foundry · GPT-4o · FastAPI · Pydantic v2 · Rich CLI

---

## Visión General del Sistema

```
┌─────────────────────────────────────────────────────────────┐
│                  Data Model Hub (Next.js)                    │
│  ┌──────────────┐   ┌──────────────┐   ┌─────────────────┐  │
│  │  Editor ER   │   │  AI Chat     │   │  Docs / Config  │  │
│  │  (canvas)    │   │  Panel       │   │                 │  │
│  └──────┬───────┘   └──────┬───────┘   └─────────────────┘  │
│         │                  │                                  │
│         └──────────────────▼──────────────────────────────── │
│                    /api/agent/* (proxy Next.js)              │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP
                            ▼
┌───────────────────────────────────────────────────────────────┐
│                  agent-modeler (FastAPI :8000)                 │
│                                                               │
│  POST /api/conversations          → crear sesión              │
│  DELETE /api/conversations/{id}   → liberar sesión            │
│  POST /api/conversations/{id}/guidelines → subir lineamientos │
│  POST /api/conversations/{id}/model      → ejecutar pipeline  │
│  GET  /api/health                 → estado del servicio       │
│  GET  /api/engines                → motores soportados        │
│                                                               │
│  ┌────────────────────────────────────────────────────────┐  │
│  │               Pipeline de Modelamiento                  │  │
│  │                                                         │  │
│  │  prepare_input → ExecutorAgent → extract_model          │  │
│  │                      ↓                                  │  │
│  │               QAValidatorAgent → format_output          │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                               │
│  Azure AI Foundry (GPT-4o) ←──── FoundryChatClient          │
└───────────────────────────────────────────────────────────────┘
```

---

## Características

- **Pipeline multi-agente**: ExecutorAgent (generación) → QAValidatorAgent (validación + estandarización)
- **5 motores de BD**: Databricks SQL, PostgreSQL, MySQL, SQL Server, CosmosDB
- **Gobierno de datos**: estandarización automática de nombres contra catálogo corporativo
- **Input flexible**: texto libre, archivos `.xlsx`, o combinación de ambos
- **Guidelines por sesión**: cada conversación puede cargar su propio archivo de lineamientos (PDF/DOCX/MD/JSON/XLSX/TXT)
- **Memoria multi-turno**: historial y último modelo inyectados como contexto en cada turno
- **CLI multi-turno**: interfaz Rich para uso directo sin web
- **Concurrencia**: múltiples sesiones simultáneas con semáforo configurable

---

## Estructura del Proyecto

```
agent-modeler/
├── api/
│   ├── main.py              # FastAPI app: lifespan, CORS, middleware, routers
│   └── routes/
│       ├── conversations.py # POST/DELETE /api/conversations + guidelines
│       ├── modeling.py      # POST/GET /api/conversations/{id}/model
│       └── health.py        # GET /api/health, GET /api/engines
├── src/
│   ├── workflow/
│   │   └── graph.py         # Pipeline: prepare_input → EA → extract_model → QA → format_output
│   ├── agents/
│   │   ├── factory.py       # Factoría de agentes (ExecutorAgent, QAValidatorAgent)
│   │   └── instructions.py  # System prompts de cada agente
│   ├── tools/
│   │   ├── knowledge_base_tools.py  # query_guidelines, get_all_guidelines
│   │   ├── catalog_tools.py         # search/add/get column catalog
│   │   ├── excel_tools.py           # parse_excel_file
│   │   └── convert_tools.py         # PDF/DOCX → Markdown (Docling)
│   ├── api/
│   │   ├── dependencies.py  # Inyección de dependencias (store, semáforo, client)
│   │   └── response_builder.py  # Mapeo workflow_result → ModelingResponseAPI
│   ├── conversation.py      # ConversationStore (en memoria, async-safe)
│   ├── schemas.py           # Pydantic models internos + contratos API
│   └── config.py            # Settings singleton (pydantic-settings)
├── prompts/                 # .prompty files (referencia, no usados en runtime)
├── data/
│   ├── column_catalog.json  # Catálogo corporativo de columnas (mutable en runtime)
│   └── guidelines/          # Archivos de lineamientos (inmutables en runtime)
├── doc/                     # Documentación técnica detallada
├── chat.py                  # CLI conversacional (Rich)
└── src/main.py              # CLI batch (Rich, sin sesiones)
```

---

## Pipeline de Modelamiento

```
Input del usuario
(texto + Excel opcional + engine)
         │
         ▼
┌─────────────────────┐
│   prepare_input     │  Construye prompt enriquecido con:
│   (Executor)        │  • Historial de conversación (si existe)
└──────────┬──────────┘  • Último modelo generado (si existe)
           │              • Tablas, columnas, relaciones, motor
           ▼
┌─────────────────────┐
│   ExecutorAgent     │  Llama get_all_guidelines()
│   (Agent · GPT-4o)  │  Genera modelo + DDL aplicando lineamientos
└──────────┬──────────┘  Responde JSON estructurado
           │
           ▼
┌─────────────────────┐
│   extract_model     │  Limpia JSON del ExecutorAgent
│   (Executor)        │  Prepara prompt para QA
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  QAValidatorAgent   │  search_column_catalog() por cada columna
│  (Agent · GPT-4o)   │  add_column_to_catalog() para columnas nuevas
└──────────┬──────────┘  Valida lineamientos, regenera DDL, quality_score
           │
           ▼
┌─────────────────────┐
│   format_output     │  Combina modelo + QA report
│   (Executor)        │  Emite JSON final
└──────────┬──────────┘
           │
           ▼
    ModelingResponseAPI
    (tablas, DDL, relaciones, qa_report, guidelines_applied)
```

---

## API REST — Referencia Completa

### `GET /api/health`

Estado del servicio.

**Response 200:**
```json
{
  "status": "ok",
  "version": "2.0.0",
  "foundry_connected": true,
  "default_engine": "databricks_sql",
  "active_conversations": 3
}
```

---

### `GET /api/engines`

Lista de motores de BD soportados.

**Response 200:**
```json
{
  "engines": ["databricks_sql", "postgresql", "mysql", "sqlserver", "cosmosdb"],
  "default": "databricks_sql"
}
```

---

### `POST /api/conversations`

Crea (o devuelve existente) una sesión de conversación. **El `conversation_id` lo genera el frontend** como UUID v4.

**Request body:**
```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "engine": "databricks_sql"
}
```

**Response 200:**
```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "engine": "databricks_sql",
  "created": true
}
```

> `created: false` si la sesión ya existía (idempotente).

---

### `DELETE /api/conversations/{conversation_id}`

Elimina la sesión y libera su memoria (historial + guidelines cacheadas).

**Response 200:**
```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "deleted": true
}
```

**Response 404** si la sesión no existe.

---

### `POST /api/conversations/{conversation_id}/guidelines`

Sube un archivo de lineamientos y lo asocia a la sesión. El archivo se procesa y cachea en memoria. Soporta: `PDF`, `DOCX`, `MD`, `JSON`, `XLSX`, `TXT`.

**Request:** `multipart/form-data` con campo `file`.

**Response 200:**
```json
{
  "status": "ok",
  "conversation_id": "550e8400-...",
  "file_name": "lineamientos_v3.docx",
  "file_format": "docx",
  "file_size_bytes": 124800,
  "preview": "# Lineamientos de Modelamiento\n\n## Nomenclatura..."
}
```

---

### `POST /api/conversations/{conversation_id}/model`

Ejecuta el pipeline completo de modelamiento. Operación cara (puede tomar 15–60s según complejidad).

**Request:** `multipart/form-data`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `context_text` | string (opcional) | Descripción en lenguaje natural o instrucción de refinamiento |
| `excel_file` | file `.xlsx` (opcional) | Excel con tablas y columnas |
| `engine` | string (opcional) | Motor destino. Si se omite, usa el de la sesión |

> Al menos uno de `context_text` o `excel_file` es requerido.

**Response 200:**
```json
{
  "conversation_id": "550e8400-...",
  "engine": "databricks_sql",
  "turn_number": 1,
  "tables": [
    {
      "table_name": "HD_CLIENTE",
      "table_description": "Tabla de clientes del sistema CRM",
      "columns": [
        {
          "column_name": "CODCLI",
          "data_type": "VARCHAR(20)",
          "nullable": false,
          "is_pk": true,
          "is_fk": false,
          "fk_references": null,
          "functional_definition": "Código único del cliente",
          "observations": ""
        }
      ],
      "ddl": "CREATE TABLE IF NOT EXISTS default.HD_CLIENTE (\n  `CODCLI` STRING NOT NULL,\n  PRIMARY KEY (`CODCLI`)\n)\nUSING DELTA\nCOMMENT 'Tabla de clientes del sistema CRM';"
    }
  ],
  "relationships": ["HD_CLIENTE → HD_ORDEN [1:N]"],
  "export_sql": "-- ── Tabla: HD_CLIENTE ──\nCREATE TABLE ...",
  "export_markdown": "# Modelo de Datos\n...",
  "qa_report": {
    "quality_score": 92,
    "standardized_columns": [],
    "guideline_violations": [],
    "new_catalog_entries": [],
    "summary": "Modelo cumple lineamientos corporativos."
  },
  "guidelines_applied": "Lineamientos de sesión: lineamientos_v3.docx (docx, 124800 bytes)",
  "summary": "Se generaron 3 tablas con 24 columnas aplicando lineamientos Databricks."
}
```

---

### `GET /api/conversations/{conversation_id}/model`

Recupera el último modelo generado sin re-ejecutar el agente.

**Response 200:** mismo schema que el POST.
**Response 404** si no hubo turnos exitosos.

---

## Gestión de Sesiones y Memoria

El `ConversationStore` (`src/conversation.py`) mantiene en memoria:

```
ConversationState {
  conversation_id: str       # UUID v4 (generado por el frontend)
  engine: str                # motor de BD activo
  history: list[{role, content}]  # historial de mensajes
  guidelines_meta: GuidelinesMeta | None  # metadatos del archivo cargado
  last_model: dict | None    # último modelo generado (inyectado como contexto)
  turn_number: int           # contador de turnos exitosos
  created_at: float
  updated_at: float
  lock: asyncio.Lock         # serializa mutaciones por sesión
}
```

**Diseño de concurrencia:**
- Un `asyncio.Lock` por sesión (serializa mutaciones dentro de la misma conversación)
- Un `asyncio.Lock` global solo para crear/borrar entradas del diccionario
- Semáforo `MAX_CONCURRENT_PIPELINES` (default 5) limita ejecuciones paralelas del pipeline

**Guidelines por sesión:**
El contenido procesado de las guidelines se almacena en `knowledge_base_tools._guidelines_cache_by_session` mediante un `ContextVar`. Al hacer `DELETE /api/conversations/{id}`, se limpian tanto el store como el cache de guidelines.

---

## Configuración

Variables de entorno en `.env`:

```env
# Azure AI Foundry
FOUNDRY_PROJECT_ENDPOINT=https://<tu-workspace>.services.ai.azure.com/api/projects/<project>
FOUNDRY_MODEL=gpt-4o

# Azure Service Principal
APP_AZURE_TENANT_ID=<tenant-uuid>
APP_AZURE_CLIENT_ID=<app-uuid>
APP_AZURE_CLIENT_SECRET=<secret>

# Datos
GUIDELINES_PATH=data/guidelines/databricks_guidelines_v3.docx
COLUMN_CATALOG_PATH=data/column_catalog.json

# Motor por defecto
DEFAULT_DB_ENGINE=databricks_sql

# Concurrencia (opcional)
MAX_CONCURRENT_PIPELINES=5

# CORS (opcional, default: localhost:3000)
CORS_ORIGINS=http://localhost:3000,https://mi-dominio.com
```

---

## Setup y Ejecución

### Requisitos
- Python 3.12+
- Acceso a Azure AI Foundry con Service Principal

### Instalación

```bash
# Crear entorno virtual
python3.12 -m venv .venv
source .venv/bin/activate   # Linux/Mac
# .venv\Scripts\activate    # Windows

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
cp .env.example .env
# Editar .env con tus credenciales
```

### Levantar la API

```bash
# Desde la raíz de agent-modeler/
source .venv/bin/activate
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 \
  --reload --reload-dir api --reload-dir src
```

> El flag `--reload-dir` evita que watchfiles recargue al detectar cambios en `.venv`.

### CLI (sin web)

```bash
# CLI batch (modo no interactivo)
python src/main.py

# CLI conversacional (multi-turno con Rich)
python chat.py
```

---

## Extracción Robusta de JSON

Los LLMs frecuentemente producen respuestas con markdown fences, texto preamble o trailing commas. `_extract_json_from_text` maneja esto con tres estrategias progresivas:

1. **Fences markdown** — busca ` ```json...``` `, extrae contenido
2. **JSON puro** — si el texto empieza con `{`, parsea directamente; si falla, busca `}` balanceado
3. **JSON embebido** — busca primer `{` y último `}` en todo el texto

En cada caso, aplica `_fix_trailing_commas` (regex: `,\s*([}\]])`) si el parse falla.

**Fix adicional:** las `relationships` devueltas como dicts (en lugar de strings) se normalizan en `response_builder.py` mediante `_normalize_relationship()` antes de llegar al frontend.

---

## Documentación Técnica Detallada

| Documento | Contenido |
|-----------|-----------|
| `doc/architecture.md` | Diagrama de componentes, capas, decisiones de diseño |
| `doc/workflow.md` | Pipeline detallado, formato de I/O de cada nodo |
| `doc/schemas.md` | Contratos Pydantic internos y de la API |
| `doc/tools.md` | Referencia de cada tool (parámetros, retorno, comportamiento) |
| `doc/configuration.md` | Variables de entorno, defaults, validación |
| `doc/usage.md` | Ejemplos de uso CLI y cURL |
