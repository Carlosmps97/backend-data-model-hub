"""API REST async para el Data Modeler Agent.

Endpoints para interactuar con el sistema desde aplicaciones externas.
"""

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# Agregar raíz del proyecto al path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.agents.factory import get_chat_client
from src.config import settings
from src.tools.excel_tools import parse_excel_file
from src.workflow.graph import run_modeling_pipeline

# Cliente compartido de AI Foundry
_chat_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestiona el ciclo de vida de la aplicación."""
    global _chat_client
    # Startup
    try:
        _chat_client = get_chat_client()
        print("✓ Conexión con Azure AI Foundry establecida")
    except Exception as e:
        print(f"⚠ Error al conectar con Azure AI Foundry: {e}")
        _chat_client = None
    yield
    # Shutdown
    print("✓ API detenida")


app = FastAPI(
    title="Data Modeler Agent API",
    description="API REST para generación de modelos de datos con validación QA",
    version="1.0.0",
    lifespan=lifespan,
)


# ─── Schemas de Request/Response ───────────────────────────────────────────


class ColumnInput(BaseModel):
    """Columna para modelar."""
    column_name: str | None = Field(None, description="Nombre sugerido")
    functional_definition: str = Field(..., description="Definición funcional obligatoria")
    data_type_hint: str | None = Field(None, description="Sugerencia de tipo de dato")
    is_nullable: bool | None = Field(None, description="¿Permite nulos?")
    notes: str | None = Field(None, description="Notas adicionales")


class TableInput(BaseModel):
    """Tabla para modelar."""
    table_name: str = Field(..., description="Nombre de la tabla")
    columns: list[ColumnInput] = Field(default_factory=list)


class ModelingRequest(BaseModel):
    """Request para generar modelo de datos."""
    user_text: str = Field(default="", description="Texto libre del usuario")
    tables: list[TableInput] = Field(default_factory=list)
    target_engine: str = Field(default="databricks_sql", description="Motor de BD destino")
    relationships: list[str] = Field(default_factory=list, description="Relaciones entre tablas")

    model_config = {
        "json_schema_extra": {
            "example": {
                "user_text": "Crea tabla de productos",
                "tables": [
                    {
                        "table_name": "productos",
                        "columns": [
                            {
                                "column_name": "nombre",
                                "functional_definition": "Nombre del producto",
                                "data_type_hint": "VARCHAR",
                                "is_nullable": False,
                            }
                        ],
                    }
                ],
                "target_engine": "postgresql",
                "relationships": [],
            }
        }
    }


class ColumnOutput(BaseModel):
    """Columna generada."""
    column_name: str
    functional_definition: str
    data_type: str
    is_nullable: bool
    is_primary_key: bool
    is_foreign_key: bool
    fk_reference: str | None
    default_value: str | None
    constraints: list[str]


class TableOutput(BaseModel):
    """Tabla generada."""
    table_name: str
    columns: list[ColumnOutput]
    ddl: str
    notes: str


class StandardizedColumn(BaseModel):
    """Columna estandarizada por QA."""
    table_name: str
    original_name: str
    standardized_name: str
    reason: str


class GuidelineViolation(BaseModel):
    """Violación de lineamiento detectada."""
    table_name: str
    column_name: str
    violation: str
    correction_applied: str


class NewCatalogEntry(BaseModel):
    """Nueva entrada en catálogo."""
    column_name: str
    functional_definition: str
    data_type: str
    table_name: str


class QAReportOutput(BaseModel):
    """Reporte de QA."""
    standardized_columns: list[StandardizedColumn]
    guideline_violations: list[GuidelineViolation]
    new_catalog_entries: list[NewCatalogEntry]
    quality_score: int
    summary: str


class ModelingResponse(BaseModel):
    """Response completo del modelamiento."""
    engine: str
    tables: list[TableOutput]
    relationships: list[str]
    summary: str
    qa_report: QAReportOutput


class ExcelParseRequest(BaseModel):
    """Request para parsear archivo Excel."""
    file_path: str = Field(..., description="Ruta absoluta al archivo .xlsx")


class HealthResponse(BaseModel):
    """Response de health check."""
    status: str
    version: str
    foundry_connected: bool
    default_engine: str


# ─── Endpoints ─────────────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Verifica el estado de la API y la conexión con Azure AI Foundry."""
    return HealthResponse(
        status="ok",
        version="1.0.0",
        foundry_connected=_chat_client is not None,
        default_engine=settings.DEFAULT_DB_ENGINE,
    )


@app.post("/model", response_model=ModelingResponse)
async def create_model(request: ModelingRequest):
    """
    Genera un modelo de datos completo con validación QA.
    
    - Recibe definiciones de tablas/columnas y motor de BD
    - Ejecuta pipeline: ExecutorAgent → QAValidatorAgent
    - Retorna modelo generado + reporte de calidad
    """
    if _chat_client is None:
        raise HTTPException(
            status_code=503,
            detail="Azure AI Foundry no está conectado. Verificar configuración.",
        )
    
    try:
        # Convertir request a dict para el pipeline
        input_data = request.model_dump()
        
        # Ejecutar pipeline
        result = await run_modeling_pipeline(_chat_client, input_data)
        
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        
        # Parsear resultado
        try:
            generated_model = json.loads(result.get("generated_model", "{}"))
            qa_validation = json.loads(result.get("qa_validation", "{}"))
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error al parsear JSON del resultado: {e}",
            )
        
        # Construir response
        qa_report_data = qa_validation.get("qa_report", {})
        qa_report = QAReportOutput(
            standardized_columns=[
                StandardizedColumn(**item)
                for item in qa_report_data.get("standardized_columns", [])
            ],
            guideline_violations=[
                GuidelineViolation(**item)
                for item in qa_report_data.get("guideline_violations", [])
            ],
            new_catalog_entries=[
                NewCatalogEntry(**item)
                for item in qa_report_data.get("new_catalog_entries", [])
            ],
            quality_score=qa_report_data.get("quality_score", 0),
            summary=qa_report_data.get("summary", ""),
        )
        
        tables = [
            TableOutput(
                table_name=t["table_name"],
                columns=[
                    ColumnOutput(**c) for c in t.get("columns", [])
                ],
                ddl=t.get("ddl", ""),
                notes=t.get("notes", ""),
            )
            for t in generated_model.get("tables", [])
        ]
        
        return ModelingResponse(
            engine=result.get("engine", request.target_engine),
            tables=tables,
            relationships=generated_model.get("relationships", []),
            summary=generated_model.get("summary", ""),
            qa_report=qa_report,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error interno del servidor: {str(e)}",
        )


@app.post("/parse-excel")
async def parse_excel(request: ExcelParseRequest) -> dict:
    """
    Parsea un archivo Excel y extrae las tablas/columnas.
    
    Útil para pre-procesar archivos antes de enviarlos a /model.
    """
    try:
        result = parse_excel_file.func(request.file_path)
        return json.loads(result)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error al parsear Excel: {str(e)}",
        )


@app.get("/engines")
async def list_engines() -> dict:
    """Lista los motores de BD soportados."""
    return {
        "engines": settings.SUPPORTED_ENGINES,
        "default": settings.DEFAULT_DB_ENGINE,
    }


# ─── Ejecución directa (para desarrollo) ────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
