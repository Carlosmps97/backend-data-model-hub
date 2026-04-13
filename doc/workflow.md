# Workflow y Pipeline de Modelamiento

## Visión general

El pipeline de modelamiento se implementa como un **grafo dirigido** usando `WorkflowBuilder` del Microsoft Agent Framework. El grafo consta de 5 nodos que procesan secuencialmente el input del usuario hasta producir un modelo validado con DDL y reporte de calidad.

**Archivo**: `src/workflow/graph.py`

---

## Grafo del pipeline

```
 ┌───────────────┐
 │ prepare_input  │  ← Executor custom
 │                │     Recibe JSON del usuario
 │ Construye      │     Extrae tablas, engine, relaciones
 │ prompt para    │     Guarda estado en WorkflowContext
 │ ExecutorAgent  │
 └───────┬───────┘
         │ AgentExecutorRequest (prompt)
         ▼
 ┌───────────────┐
 │ ExecutorAgent  │  ← Agent del framework
 │                │     Llama a query_guidelines
 │ Genera modelo  │     Genera JSON con tablas + DDL
 │ de datos + DDL │     Responde con modelo completo
 └───────┬───────┘
         │ AgentExecutorResponse (modelo JSON)
         ▼
 ┌───────────────┐
 │ extract_model  │  ← Executor custom
 │                │     Almacena modelo en ctx.state
 │ Extrae JSON    │     Construye prompt de validación
 │ Prepara prompt │     para QAValidatorAgent
 │ para QA Agent  │
 └───────┬───────┘
         │ AgentExecutorRequest (prompt QA)
         ▼
 ┌───────────────┐
 │ QAValidator    │  ← Agent del framework
 │ Agent          │     Llama a search_column_catalog
 │                │     Llama a add_column_to_catalog
 │ Valida +       │     Llama a query_guidelines
 │ estandariza    │     Responde con QA report
 └───────┬───────┘
         │ AgentExecutorResponse (QA JSON)
         ▼
 ┌───────────────┐
 │ format_output  │  ← Executor custom
 │                │     Combina modelo + QA
 │ Limpia JSON    │     Aplica _extract_json_from_text
 │ Combina        │     Emite resultado final
 │ resultado      │
 └───────────────┘
         │
         ▼
    JSON resultado final
```

---

## Nodos del pipeline

### 1. `prepare_input` (Executor)

**Entrada**: JSON string con datos del usuario.

**Proceso**:
1. Parsea el JSON de entrada.
2. Extrae: `tables`, `user_text`, `target_engine`, `relationships`.
3. Construye un prompt Markdown estructurado con secciones:
   - `## SOLICITUD DE MODELAMIENTO DE DATOS` — motor destino
   - `## CONTEXTO DEL USUARIO` — texto libre
   - `## TABLAS A MODELAR` — columnas con nombre sugerido, definición, tipo, nullable, notas
   - `## RELACIONES ENTRE TABLAS` — FK declaradas por el usuario
   - `## INSTRUCCIONES` — pasos para el ExecutorAgent
4. Guarda `input_data` y `target_engine` en `WorkflowContext`.
5. Envía `AgentExecutorRequest` con el prompt.

**Formato del input JSON**:
```json
{
  "user_text": "Crea una tabla de productos...",
  "tables": [
    {
      "table_name": "productos",
      "columns": [
        {
          "column_name": "nombre",
          "functional_definition": "Nombre del producto",
          "data_type_hint": "VARCHAR",
          "is_nullable": false,
          "notes": ""
        }
      ]
    }
  ],
  "target_engine": "postgresql",
  "relationships": ["productos tiene FK hacia categorias"]
}
```

### 2. `ExecutorAgent` (Agent)

**Entrada**: prompt Markdown del paso anterior.

**Proceso**:
1. Lee el prompt con la solicitud de modelamiento.
2. Invoca `query_guidelines` y/o `get_all_guidelines` para obtener lineamientos.
3. Genera el modelo de datos completo:
   - Nombres de tablas con prefijo `tbl_`.
   - Nombres de columnas en snake_case con prefijos semánticos.
   - Tipos de dato mapeados al motor destino.
   - Columnas de auditoría.
   - Claves primarias y foráneas.
   - DDL ejecutable.
4. Responde con JSON estructurado.

**Salida**: JSON del modelo generado (puede contener markdown fences o texto preamble).

### 3. `extract_model` (Executor)

**Entrada**: `AgentExecutorResponse` del ExecutorAgent.

**Proceso**:
1. Obtiene `response.agent_response.text`.
2. Guarda el modelo raw en `ctx.state["generated_model"]`.
3. Construye prompt de validación para QAValidatorAgent con:
   - El modelo generado completo.
   - El motor de BD.
   - Instrucciones de validación detalladas (7 pasos).
4. Envía `AgentExecutorRequest` con el prompt de QA.

### 4. `QAValidatorAgent` (Agent)

**Entrada**: prompt con modelo a validar + instrucciones.

**Proceso**:
1. Consulta lineamientos con `query_guidelines`.
2. Para cada columna del modelo:
   - Llama a `search_column_catalog` con la definición funcional.
   - Si match ≥ 0.5 → reemplaza nombre.
   - Si no hay match → llama a `add_column_to_catalog`.
3. Verifica cumplimiento de lineamientos (naming, tipos, auditoría, PK).
4. Regenera DDL si hubo correcciones.
5. Calcula quality_score (0-100).
6. Responde con JSON del QA report.

**Salida**: JSON con tablas corregidas + qa_report.

### 5. `format_output` (Executor)

**Entrada**: `AgentExecutorResponse` del QAValidatorAgent.

**Proceso**:
1. Obtiene resultado QA y modelo original del estado.
2. Aplica `_extract_json_from_text` para limpiar ambas respuestas.
3. Construye JSON final:
```json
{
  "engine": "postgresql",
  "generated_model": "{ ... JSON limpio del modelo ... }",
  "qa_validation": "{ ... JSON limpio del QA report ... }"
}
```
4. Emite resultado con `ctx.yield_output`.

---

## Estado del workflow (WorkflowContext)

El estado se gestiona con `ctx.set_state()` y `ctx.get_state()`:

| Clave | Tipo | Establecido por | Consumido por |
|-------|------|-----------------|---------------|
| `input_data` | `dict` | `prepare_input` | (disponible) |
| `target_engine` | `str` | `prepare_input` | `extract_model`, `format_output` |
| `generated_model` | `str` | `extract_model` | `format_output` |

---

## Extracción robusta de JSON

### Problema
Los LLMs frecuentemente producen respuestas con:
- Texto preamble antes del JSON ("Aquí está el modelo generado:\n```json\n{...}")
- Markdown fences (`` ```json ... ``` ``)
- Trailing commas (`{"key": "value",}`)
- Texto después del JSON

### Solución: `_extract_json_from_text(text)`

Tres estrategias progresivas:

**Caso 1 — JSON en fences**:
```
Busca: ```json\n{...}\n```
Extrae contenido entre fences.
```

**Caso 2 — JSON puro**:
```
Si el texto empieza con {, intenta parsear directamente.
Si falla, busca el último } balanceado (conteo de profundidad).
```

**Caso 3 — JSON embebido en texto**:
```
Busca primer { y último } en todo el texto.
Extrae la subcadena y valida.
```

En cada caso, si el parse directo falla, aplica `_fix_trailing_commas` y reintenta.

### `_fix_trailing_commas(text)`
Regex: `r',\s*([}\]])'` → elimina comas antes de `}` o `]`.

---

## Creación y ejecución

### Crear el workflow

```python
from src.workflow.graph import create_modeling_workflow
from src.agents.factory import get_chat_client

client = get_chat_client()
workflow = create_modeling_workflow(client)
```

Internamente, `create_modeling_workflow`:
1. Crea `ExecutorAgent` y `QAValidatorAgent` con el cliente compartido.
2. Construye el grafo con `WorkflowBuilder`:
```python
workflow = (
    WorkflowBuilder(start_executor=prepare_input)
    .add_edge(prepare_input, executor_agent)
    .add_edge(executor_agent, extract_model)
    .add_edge(extract_model, qa_agent)
    .add_edge(qa_agent, format_output)
    .build()
)
```

### Ejecutar el pipeline

```python
from src.workflow.graph import run_modeling_pipeline

result = await run_modeling_pipeline(client, input_data)
# result = {"engine": "...", "generated_model": "...", "qa_validation": "..."}
```

La función `run_modeling_pipeline`:
1. Crea el workflow.
2. Serializa input a JSON.
3. Ejecuta `workflow.run(input_json)`.
4. Extrae outputs del resultado.
5. Retorna dict parseado o `{"error": "..."}`.

---

## Flujo de datos completo

```
 Usuario
   │
   │  "Crea tabla productos para postgresql"
   │
   ▼
 main.py
   │  Extrae: engine="postgresql", tables=[], relationships=[]
   │  Construye: input_data = {user_text, tables, target_engine, relationships}
   │
   ▼
 run_modeling_pipeline(client, input_data)
   │
   ├─ prepare_input
   │    Prompt: "## SOLICITUD DE MODELAMIENTO\nMotor: postgresql\n..."
   │    State: input_data={...}, target_engine="postgresql"
   │
   ├─ ExecutorAgent
   │    Tool calls: query_guidelines("naming_conventions"), query_guidelines("postgresql")
   │    Response: {"tables": [...], "ddl": "CREATE TABLE...", ...}
   │
   ├─ extract_model
   │    State: generated_model="{ respuesta del executor }"
   │    Prompt QA: "## VALIDACIÓN\n{modelo}\n## INSTRUCCIONES\n..."
   │
   ├─ QAValidatorAgent
   │    Tool calls: search_column_catalog("Nombre del producto"), add_column_to_catalog(...)
   │    Response: {"tables": [...], "qa_report": {...}}
   │
   └─ format_output
        Clean: _extract_json_from_text(modelo), _extract_json_from_text(qa)
        Output: {"engine": "postgresql", "generated_model": "...", "qa_validation": "..."}
   │
   ▼
 main.py → _display_results(result)
   │  Rich tables, DDL panels, QA score panel
   ▼
 Console output
```
