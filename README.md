# 🏗️ Data Modeler Agent

Sistema multi-agente experto en modelamiento de datos construido con el **Microsoft Agent Framework** (Python 3.12). Genera modelos de datos profesionales, DDL ejecutable y reportes de calidad a partir de definiciones funcionales en lenguaje natural o archivos Excel.

> **Stack**: Microsoft Agent Framework · Azure AI Foundry · GPT-4o · Pydantic v2 · Rich CLI

---

## Características principales

- **Multi-agente**: pipeline orquestado con ExecutorAgent (generación) y QAValidatorAgent (validación).
- **5 motores de BD**: Databricks SQL, Cosmos DB, SQL Server, PostgreSQL, MySQL.
- **Gobierno de datos**: estandarización automática de nombres contra catálogo corporativo.
- **Input flexible**: texto libre, archivos `.xlsx` o combinación de ambos.
- **Lineamientos configurables**: naming conventions, tipos de dato, columnas de auditoría (JSON/XLSX/DOCX/PDF/TXT).
- **CLI multi-turno**: interfaz conversacional con Rich para refinamiento iterativo.

---

## Arquitectura

```mermaid
flowchart TB
    subgraph Entrada["📥 Entrada"]
        U[Usuario<br/>Texto / .xlsx]
    end

    subgraph CapaUI["💻 Capa de Presentación"]
        CA[ConversationalAgent<br/>• Parseo input<br/>• Detección motor BD<br/>• Formateo Rich]
    end

    subgraph Knowledge["📚 KnowledgeBase"]
        KB[Tools compartidas<br/>query_guidelines<br/>get_all_guidelines]
    end

    subgraph Workflow["⚙️ Workflow Pipeline"]
        PI[prepare_input]
        EA[ExecutorAgent]
        EM[extract_model]
        QA[QAValidatorAgent]
        FO[format_output]
    end

    subgraph Salida["📤 Salida"]
        DM[(Modelo de datos<br/>+ DDL ejecutable)]
        CAT[(Catálogo corporativo<br/>actualizado)]
    end

    U --> CA
    CA --> PI
    PI --> EA
    EA -.->|consulta| KB
    EA --> EM
    EM --> QA
    QA -.->|consulta| KB
    QA -.->|actualiza| CAT
    QA --> FO
    FO --> DM

    style Entrada fill:#e1f5fe
    style CapaUI fill:#fff3e0
    style Knowledge fill:#f3e5f5
    style Workflow fill:#e8f5e9
    style Salida fill:#ffebee
```

---

## Quick Start

```bash
# 1. Clonar y entrar al proyecto
cd agent-modeler

# 2. Crear entorno virtual (Python 3.12)
python3.12 -m venv .venv
source .venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar credenciales
cp .env.example .env
# Editar .env con tus credenciales de Azure

# 5. Crear archivo de ejemplo (opcional)
python scripts/create_sample_xlsx.py

# 6. Ejecutar
python -m src.main
```

### Ejemplos de input

```
# Con archivo Excel
Modela las tablas del archivo data/sample_input.xlsx para databricks_sql

# Solo texto
Crea una tabla de productos con: id, nombre, precio, categoría, stock. Motor: postgresql

# Con relaciones
Crea tablas customers y orders. La tabla orders tiene FK hacia customers por customer_id. Motor: sqlserver
```

---

## Agentes y Tools

| Agente | Rol | Tools |
|--------|-----|-------|
| **ConversationalAgent** | Orquestador, UI multi-turno | `parse_excel_file` |
| **KnowledgeBaseAgent** | Lineamientos corporativos (como tools compartidas) | — |
| **ExecutorAgent** | Genera modelo de datos + DDL | `query_guidelines`, `get_all_guidelines` |
| **QAValidatorAgent** | Valida, estandariza, gobierno de datos | `search_column_catalog`, `add_column_to_catalog`, `get_full_column_catalog`, `query_guidelines` |

### Motores de BD soportados

| Motor | Dialecto | Características |
|-------|----------|-----------------|
| **databricks_sql** | Delta Lake SQL | `USING DELTA`, `STRING` en vez de `VARCHAR` |
| **cosmosdb** | NoSQL JSON | Container definition, partition key |
| **sqlserver** | T-SQL | `IDENTITY`, esquemas `[dbo]` |
| **postgresql** | PostgreSQL | `SERIAL`/`BIGSERIAL`, esquemas |
| **mysql** | MySQL | `AUTO_INCREMENT`, `ENGINE=InnoDB` |

---

## Estructura del proyecto

```
agent-modeler/
├── .env                              # Variables de entorno (no versionado)
├── .env.example                      # Template de configuración
├── requirements.txt                  # Dependencias Python
├── README.md
├── doc/                              # 📚 Documentación detallada
│   ├── architecture.md               #   Arquitectura del sistema
│   ├── agents.md                     #   Agentes: roles, prompts, tools
│   ├── tools.md                      #   Referencia de tools
│   ├── workflow.md                   #   Pipeline y flujos de datos
│   ├── schemas.md                    #   Modelos Pydantic
│   ├── configuration.md             #   Guía de configuración
│   └── usage.md                      #   Guía de uso y ejemplos
├── data/
│   ├── column_catalog.json           # Catálogo corporativo de columnas
│   ├── sample_input.xlsx             # Archivo de ejemplo
│   └── guidelines/
│       └── modeling_guidelines.json  # Lineamientos de modelamiento
├── src/
│   ├── config.py                     # Configuración centralizada (Settings)
│   ├── schemas.py                    # Modelos Pydantic
│   ├── main.py                       # Entry point CLI multi-turno
│   ├── tools/
│   │   ├── knowledge_base_tools.py   # Carga/consulta de lineamientos
│   │   ├── excel_tools.py            # Parser robusto de Excel
│   │   └── catalog_tools.py          # CRUD catálogo + similitud
│   ├── agents/
│   │   ├── factory.py                # Factoría de agentes
│   │   └── instructions.py           # System prompts detallados
│   └── workflow/
│       └── graph.py                  # Workflow (grafo dirigido)
└── scripts/
    ├── create_sample_xlsx.py         # Generador de datos de ejemplo
    └── test_pipeline_v2.py           # Test end-to-end del pipeline
```

---

## 📚 Documentación

| Documento | Descripción |
|-----------|-------------|
| [Arquitectura](doc/architecture.md) | Diseño del sistema, componentes, decisiones de arquitectura |
| [Agentes](doc/agents.md) | Roles, instrucciones, tools asignadas a cada agente |
| [Tools](doc/tools.md) | Referencia completa de cada tool con parámetros y ejemplos |
| [Workflow](doc/workflow.md) | Pipeline de modelamiento, flujo de datos, grafo dirigido |
| [Schemas](doc/schemas.md) | Modelos Pydantic: input, output, catálogo, QA report |
| [Configuración](doc/configuration.md) | Variables de entorno, credenciales Azure, rutas de datos |
| [Uso](doc/usage.md) | Guía de uso del CLI, ejemplos, comandos disponibles |

---

## Licencia

Proyecto interno — uso corporativo.
