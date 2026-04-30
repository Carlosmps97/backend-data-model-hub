# Arquitectura del Sistema — Data Modeler Agent

## Visión General

El sistema se compone de dos procesos independientes que se comunican vía HTTP:

```
┌──────────────────────────────────────────────────────────────────┐
│              Data Model Hub  (Next.js · puerto 3000)             │
│                                                                  │
│  ┌──────────────┐  ┌─────────────────┐  ┌─────────────────────┐ │
│  │ Canvas ER    │  │  AI Chat Panel  │  │  Docs / Settings    │ │
│  │ (React Flow) │  │  (useAgentStore)│  │                     │ │
│  └──────┬───────┘  └────────┬────────┘  └─────────────────────┘ │
│         │                   │                                    │
│         └───────────────────▼─────────────────────────────────── │
│                  /api/agent/* — proxy Route Handlers             │
│              /api/projects, /api/models — JSON storage           │
└──────────────────────────┬───────────────────────────────────────┘
                           │ HTTP / multipart-form
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│           agent-modeler  (FastAPI · puerto 8000)                 │
│                                                                  │
│  Endpoints:                                                      │
│    POST   /api/conversations          crear sesión               │
│    DELETE /api/conversations/{id}     liberar sesión             │
│    POST   /api/conversations/{id}/guidelines  subir guidelines   │
│    POST   /api/conversations/{id}/model       ejecutar pipeline  │
│    GET    /api/conversations/{id}/model       último modelo      │
│    GET    /api/health                         estado             │
│    GET    /api/engines                        motores soportados │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  Pipeline (WorkflowBuilder — Microsoft Agent Framework)    │ │
│  │                                                            │ │
│  │  prepare_input → ExecutorAgent → extract_model             │ │
│  │                                       ↓                    │ │
│  │                               QAValidatorAgent             │ │
│  │                                       ↓                    │ │
│  │                                 format_output              │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ConversationStore (in-memory, async-safe)                       │
│  Azure AI Foundry ← FoundryChatClient ← Service Principal       │
└──────────────────────────────────────────────────────────────────┘
```

---

## Capas del Sistema

### 1. Capa de Presentación

**Frontend (Next.js `web-data-model-hub/`):**

| Componente | Archivo | Responsabilidad |
|-----------|---------|-----------------|
| Canvas ER | `features/diagram/DiagramCanvas.tsx` | React Flow, TableNode, ViewNode, tool sidebar, connect mode |
| TableForm | `features/table-editor/TableForm.tsx` | Formulario completo de tabla: lógico/físico, dominio, columnas, partición |
| ViewForm | `features/views/ViewForm.tsx` | Crear/editar vistas SQL con alias, expresiones, WHERE, custom SQL |
| DomainManager | `features/domains/DomainManager.tsx` | CRUD catálogo de dominios y subdominios |
| ChatPanel | `features/chat-agent/ChatPanel.tsx` | UI del chat con el agente IA |
| AgentDogState | `features/chat-agent/AgentDogState.tsx` | Animaciones de estado del agente |
| useModelStore | `store/useModelStore.ts` | Estado global: tablas, relaciones, vistas, dominios, guardado |
| useAgentStore | `store/useAgentStore.ts` | Sesiones del agente IA por modelId |

**CLI (`src/main.py`, `chat.py`):** interfaz multi-turno con Rich. Usa `run_modeling_pipeline` directamente, sin sesiones explícitas ni `ConversationStore`.

---

### 2. Capa de API (FastAPI)

```
api/
├── main.py          # lifespan, CORS, middleware de logging, routers
└── routes/
    ├── health.py        # GET /api/health, GET /api/engines
    ├── conversations.py # POST/DELETE sesión + POST guidelines
    └── modeling.py      # POST/GET /api/conversations/{id}/model
```

**Lifespan (`main.py`):**
1. Instancia `ConversationStore` → guardado en `app.state.store`
2. Crea `FoundryChatClient` → guardado en `app.state.chat_client` (puede ser `None` si falla, responde 503)
3. Crea `asyncio.Semaphore(MAX_CONCURRENT_PIPELINES)` → limita ejecuciones paralelas

**Middleware de logging:** loguea inicio/fin de cada request con `request_id`, path, `conversation_id` extraído del path y duración en ms.

---

### 3. Capa de Orquestación — Pipeline (`src/workflow/graph.py`)

Grafo dirigido implementado con `WorkflowBuilder` del Microsoft Agent Framework:

```
prepare_input  ──▶  ExecutorAgent  ──▶  extract_model
                                              │
                                              ▼
                                      QAValidatorAgent
                                              │
                                              ▼
                                        format_output
                                              │
                                              ▼
                                         JSON final
```

**Estado del workflow (`WorkflowContext`):**

| Clave | Tipo | Establecido por | Consumido por |
|-------|------|-----------------|---------------|
| `input_data` | `dict` | `prepare_input` | (disponible para debug) |
| `target_engine` | `str` | `prepare_input` | `extract_model`, `format_output` |
| `generated_model` | `str` | `extract_model` | `format_output` |

**Inyección de contexto multi-turno:**

`prepare_input` construye el prompt con secciones opcionales:
- `## HISTORIAL DE CONVERSACIÓN PREVIA` — solo si la API pasó `history` no vacío
- `## MODELO ACTUAL` — solo si la API pasó `last_model` no vacío (el agente lo toma como base y aplica solo los cambios solicitados)

El `session_id` nunca aparece en el prompt — se usa solo para resolver el cache de guidelines vía `ContextVar`.

---

### 4. Capa de Agentes (`src/agents/`)

**Factoría (`factory.py`)** crea agentes con el cliente compartido:

| Agente | Tools disponibles | Rol |
|--------|-------------------|-----|
| `ExecutorAgent` | `query_guidelines`, `get_all_guidelines`, `convert_to_markdown` | Genera modelo + DDL aplicando lineamientos |
| `QAValidatorAgent` | `query_guidelines`, `search_column_catalog`, `add_column_to_catalog`, `get_full_column_catalog`, `convert_to_markdown` | Valida, estandariza nombres, calcula quality_score |
| `ConversationalAgent` | `parse_excel_file` | Orquestador CLI (no usado en pipeline web) |

---

### 5. Capa de Tools (`src/tools/`)

```python
# knowledge_base_tools.py
query_guidelines(query: str) -> str          # busca en los lineamientos cargados
get_all_guidelines() -> str                  # retorna el documento completo

# catalog_tools.py
search_column_catalog(definition: str) -> str  # Jaccard similarity ≥ 0.3 → candidatos
add_column_to_catalog(name, definition, type, table) -> str
get_full_column_catalog() -> str

# excel_tools.py
parse_excel_file(file_path: str) -> str      # parsea .xlsx, header matching flexible

# convert_tools.py
convert_to_markdown(file_path: str) -> str   # PDF/DOCX → Markdown con Docling + caché
```

**Aislamiento de guidelines por sesión:**

```python
# Patrón ContextVar para guidelines por sesión
_session_id_var: ContextVar[str | None] = ContextVar('session_id', default=None)

@contextmanager
def session_scope(session_id: str | None):
    token = _session_id_var.set(session_id)
    try:
        yield
    finally:
        _session_id_var.reset(token)

# Al ejecutar el pipeline:
with session_scope(session_id):
    events = await workflow.run(input_json)
```

---

### 6. Capa de Memoria — `ConversationStore` (`src/conversation.py`)

Store en memoria (sin persistencia, por diseño):

```python
@dataclass
class ConversationState:
    conversation_id: str
    engine: str
    history: list[dict[str, str]]     # [{role, content}]
    guidelines_meta: GuidelinesMeta | None
    last_model: dict | None            # inyectado en próximo turno
    turn_number: int                   # se incrementa en cada turno exitoso
    created_at: float
    updated_at: float
    lock: asyncio.Lock                 # serializa mutaciones por sesión
```

**Diseño de concurrencia:**
- `_root_lock` (1 global) para crear/borrar entradas del dict
- `state.lock` (1 por sesión) para mutaciones dentro de la sesión
- Semáforo `MAX_CONCURRENT_PIPELINES` (default 5) en app.state

---

### 7. Capa de Respuesta — `response_builder.py`

Transforma el output crudo del workflow en `ModelingResponseAPI`:

```
workflow_result: {engine, generated_model: str, qa_validation: str}
         │
         ▼
_parse_json() — tolera dicts ya parseados y errores
_map_column() — mapea nombres internos (is_primary_key → is_pk, etc.)
_map_table()
_map_qa_report()
_normalize_relationship() — convierte dicts a strings si el LLM los generó mal
_build_export_sql() — concatena DDLs
_build_export_markdown() — genera Markdown estructurado
_summarize_guidelines_applied()
         │
         ▼
ModelingResponseAPI (contrato del frontend)
```

---

## Flujo End-to-End Completo

```
1. Frontend genera UUID v4 como conversation_id

2. Frontend: POST /api/conversations {conversation_id, engine}
   Backend: ConversationStore.get_or_create() → devuelve {created: true}

3. (Opcional) Frontend: POST /api/conversations/{id}/guidelines
   Backend: procesa archivo → cachea en _guidelines_cache_by_session[session_id]
            → guarda GuidelinesMeta en ConversationState

4. Frontend: POST /api/conversations/{id}/model {context_text, excel_file?, engine?}
   Backend:
     a. Lee ConversationState: history, last_model, engine
     b. Adquiere semáforo (MAX_CONCURRENT_PIPELINES)
     c. Llama run_modeling_pipeline({...input_data, history, last_model, session_id})
     d. session_scope(session_id) → ContextVar listo para guidelines
     e. Pipeline ejecuta 5 nodos (≈15-60s según complejidad)
     f. response_builder.build_modeling_response()
     g. ConversationStore.append_message(user + assistant)
     h. ConversationStore.set_last_model(model_payload)
   Frontend: recibe ModelingResponseAPI → muestra en chat

5. Usuario: "Aplicar al canvas" (2-step confirm)
   Frontend: setTables() + setRelationships() en useModelStore

6. Usuario: click "Guardar"
   Frontend: PUT /api/models/{id} {tables, relationships, views, domainCatalog}
   Next.js API: jsonStore.updateModel() → escribe JSON en disco

7. (Opcional) Frontend: DELETE /api/conversations/{id}
   Backend: ConversationStore.clear() + clear_session_guidelines()
```

---

## Decisiones de Diseño

### Por qué UUID generado por el frontend

El `conversation_id` es generado por el frontend (UUID v4 estricto validado por regex en el backend). Esto permite:
- El frontend puede crear la sesión de forma optimista sin round-trip previo
- El UUID es predecible desde el frontend para logging/debugging
- El backend puede rechazar IDs malformados con 422 antes de tocar el store

### Por qué guidelines como ContextVar

Las guidelines son por sesión pero el pipeline corre en un contexto asyncio diferente al request. `ContextVar` propaga el `session_id` dentro del scope del pipeline sin necesidad de pasarlo como parámetro a cada tool, manteniendo las tools agnósticas de la sesión.

### Por qué el store es in-memory (sin BD)

Por diseño explícito: la plataforma es conversacional y las sesiones son efímeras. La memoria de conversación (historial, guidelines, último modelo) solo es relevante mientras el usuario trabaja. El modelo persistido vive en el JSON del frontend.

### Por qué `_normalize_relationship`

El LLM a veces devuelve `relationships` como lista de dicts `{from_table, to_table, description}` en lugar de strings. `_normalize_relationship` aplana cualquier formato a string legible (`tabla_a.col → tabla_b.col [tipo] — descripción`) sin romper el contrato `list[str]` del schema API.

---

## Diagrama de Componentes Detallado

```
┌─────────────────────────────────────────────────────────────────┐
│  Presentation                                                    │
│  ┌──────────────┐  ┌──────────────────────────────────────────┐ │
│  │  CLI         │  │  FastAPI REST API                        │ │
│  │  src/main.py │  │  api/main.py + api/routes/               │ │
│  │  chat.py     │  │                                          │ │
│  └──────┬───────┘  └──────────────────┬───────────────────────┘ │
└─────────┼─────────────────────────────┼─────────────────────────┘
          │                             │
          └──────────────┬──────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Orchestration  (src/workflow/graph.py)                         │
│                                                                  │
│  run_modeling_pipeline(client, input_data)                       │
│    └── create_modeling_workflow(client)                          │
│          └── WorkflowBuilder                                     │
│                .add_edge(prepare_input, executor_agent)          │
│                .add_edge(executor_agent, extract_model)          │
│                .add_edge(extract_model, qa_agent)                │
│                .add_edge(qa_agent, format_output)                │
│                .build()                                          │
└─────────────────────────────────────────────────────────────────┘
          │                             │
          ▼                             ▼
┌─────────────────────┐   ┌────────────────────────────────────────┐
│  ExecutorAgent      │   │  QAValidatorAgent                      │
│  (src/agents/)      │   │  (src/agents/)                         │
│                     │   │                                        │
│  Tools:             │   │  Tools:                                │
│  • get_all_guidelines│  │  • query_guidelines                   │
│  • query_guidelines  │  │  • search_column_catalog              │
│  • convert_to_markdown│ │  • add_column_to_catalog              │
└─────────┬───────────┘   │  • get_full_column_catalog            │
          │               │  • convert_to_markdown                │
          │               └────────────────┬───────────────────────┘
          │                                │
          ▼                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  Tools Layer  (src/tools/)                                       │
│                                                                  │
│  knowledge_base_tools ──▶ guidelines file (PDF/DOCX → .md)      │
│  catalog_tools        ──▶ data/column_catalog.json              │
│  excel_tools          ──▶ .xlsx input                           │
│  convert_tools        ──▶ Docling conversion + local cache      │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│  Infrastructure                                                  │
│                                                                  │
│  src/config.py   → Settings (pydantic-settings, .env)           │
│  Azure Identity  → ClientSecretCredential (Service Principal)   │
│  FoundryChatClient → Azure AI Foundry endpoint + GPT-4o         │
└─────────────────────────────────────────────────────────────────┘
```
