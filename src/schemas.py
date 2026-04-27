"""Modelos Pydantic para las estructuras de datos internas y de la API.

Dos grupos:
- **Internos**: representan tablas, columnas, catálogo, QA report, etc., tal
  como los manejan los agentes y el workflow. Se mantienen sin cambios para
  no romper el CLI.
- **API**: contratos de request/response que la API REST expone al frontend
  (Next.js). Definidos al final del archivo con sufijo `API`.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


# ─── Modelos para el catálogo de columnas ──────────────────────


class CatalogColumn(BaseModel):
    """Columna registrada en el catálogo corporativo."""

    column_name: str = Field(description="Nombre estandarizado de la columna")
    functional_definition: str = Field(description="Definición funcional de la columna")
    data_type: str = Field(description="Tipo de dato base")
    used_in_tables: list[str] = Field(default_factory=list, description="Tablas donde se usa")
    is_new: bool = Field(default=False, description="Si fue agregada recientemente")


class ColumnCatalog(BaseModel):
    """Catálogo completo de columnas corporativas."""

    columns: list[CatalogColumn] = Field(default_factory=list)


# ─── Modelos para input del usuario ───────────────────────────


class RawColumnInput(BaseModel):
    """Columna tal como viene del Excel del usuario."""

    column_name: str | None = Field(default=None, description="Nombre sugerido (opcional)")
    functional_definition: str = Field(description="Definición funcional obligatoria")
    data_type_hint: str | None = Field(default=None, description="Sugerencia de tipo de dato")
    is_nullable: bool | None = Field(default=None, description="¿Permite nulos?")
    notes: str | None = Field(default=None, description="Notas adicionales")


class RawTableInput(BaseModel):
    """Tabla tal como viene del Excel del usuario (una pestaña)."""

    table_name: str = Field(description="Nombre de la pestaña / tabla candidata")
    columns: list[RawColumnInput] = Field(default_factory=list)


class UserInput(BaseModel):
    """Input completo del usuario: tablas + contexto."""

    tables: list[RawTableInput] = Field(default_factory=list)
    user_text: str = Field(default="", description="Texto libre del usuario")
    target_engine: str = Field(default="databricks_sql", description="Motor de BD destino")
    relationships: list[str] = Field(default_factory=list, description="Relaciones entre tablas")


# ─── Modelos para el modelo de datos generado ─────────────────


class ColumnDefinition(BaseModel):
    """Definición completa de una columna del modelo generado."""

    column_name: str = Field(description="Nombre de la columna")
    functional_definition: str = Field(description="Definición funcional")
    data_type: str = Field(description="Tipo de dato para el motor destino")
    is_nullable: bool = Field(default=True)
    is_primary_key: bool = Field(default=False)
    is_foreign_key: bool = Field(default=False)
    fk_reference: str | None = Field(default=None, description="Tabla.columna referenciada")
    default_value: str | None = Field(default=None)
    constraints: list[str] = Field(default_factory=list)


class TableModel(BaseModel):
    """Modelo completo de una tabla generada."""

    table_name: str = Field(description="Nombre final de la tabla")
    columns: list[ColumnDefinition] = Field(default_factory=list)
    ddl: str = Field(default="", description="DDL generado")
    notes: str = Field(default="")


class DataModelOutput(BaseModel):
    """Salida completa del ExecutorAgent."""

    tables: list[TableModel] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)
    engine: str = Field(default="databricks_sql")
    summary: str = Field(default="")


# ─── Modelos para el reporte de QA ────────────────────────────


class ColumnStandardization(BaseModel):
    """Registro de estandarización de una columna."""

    table_name: str
    original_name: str = Field(description="Nombre propuesto por el ExecutorAgent")
    standardized_name: str = Field(description="Nombre corregido del catálogo")
    reason: str = Field(description="Razón de la corrección")


class GuidelineViolation(BaseModel):
    """Violación de lineamiento detectada."""

    table_name: str
    column_name: str
    violation: str
    correction_applied: str


class NewCatalogEntry(BaseModel):
    """Nueva entrada agregada al catálogo."""

    column_name: str
    functional_definition: str
    data_type: str
    table_name: str


class QAReport(BaseModel):
    """Reporte completo de validación del QA Agent."""

    standardized_columns: list[ColumnStandardization] = Field(default_factory=list)
    guideline_violations: list[GuidelineViolation] = Field(default_factory=list)
    new_catalog_entries: list[NewCatalogEntry] = Field(default_factory=list)
    quality_score: int = Field(default=0, ge=0, le=100)
    summary: str = Field(default="")


class QAValidationOutput(BaseModel):
    """Salida completa del QAValidatorAgent."""

    tables: list[TableModel] = Field(default_factory=list)
    qa_report: QAReport = Field(default_factory=QAReport)


# ─── Modelo para el resultado final del workflow ──────────────


class WorkflowResult(BaseModel):
    """Resultado final que se presenta al usuario."""

    data_model: DataModelOutput = Field(default_factory=DataModelOutput)
    qa_validation: QAValidationOutput = Field(default_factory=QAValidationOutput)
    final_ddls: dict[str, str] = Field(
        default_factory=dict, description="Mapa tabla_name → DDL corregido"
    )
    relationship_diagram: str = Field(default="", description="Diagrama textual de relaciones")


# ════════════════════════════════════════════════════════════════════════
# ─── Schemas de la API REST (consumidos por el frontend Next.js) ────────
# ════════════════════════════════════════════════════════════════════════


# UUID v4 estricto: 8-4-4-4-12 hex chars con la versión 4 fija.
# Aceptamos uppercase y lowercase; los UUIDs canónicos son lowercase.
UUIDv4 = Annotated[
    str,
    StringConstraints(
        pattern=(
            r"^[0-9a-fA-F]{8}-"
            r"[0-9a-fA-F]{4}-"
            r"4[0-9a-fA-F]{3}-"
            r"[89abAB][0-9a-fA-F]{3}-"
            r"[0-9a-fA-F]{12}$"
        ),
        strip_whitespace=True,
    ),
]


# ─── Conversaciones ──────────────────────────────────────────────────────


class CreateConversationRequest(BaseModel):
    """Body de POST /api/conversations."""

    conversation_id: UUIDv4 = Field(
        description="UUID v4 generado por el frontend al abrir el chat."
    )
    engine: str = Field(
        default="databricks_sql",
        description="Motor de BD inicial para la sesión.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
                "engine": "databricks_sql",
            }
        }
    )


class CreateConversationResponse(BaseModel):
    """Respuesta de POST /api/conversations."""

    conversation_id: UUIDv4
    engine: str
    created: bool = Field(
        description="True si la conversación se creó; False si ya existía."
    )


class GuidelinesUploadResponse(BaseModel):
    """Respuesta de POST /api/conversations/{id}/guidelines."""

    status: str = Field(default="ok")
    conversation_id: UUIDv4
    file_name: str
    file_format: str = Field(
        description="Formato detectado: json, xlsx, docx, pdf, md, txt."
    )
    file_size_bytes: int
    preview: str = Field(
        description="Primeros 500 caracteres del contenido procesado."
    )


class DeleteConversationResponse(BaseModel):
    """Respuesta de DELETE /api/conversations/{id}."""

    conversation_id: UUIDv4
    deleted: bool


# ─── Generación de modelo ────────────────────────────────────────────────


class ColumnAPI(BaseModel):
    """Columna del modelo expuesta al frontend."""

    column_name: str
    data_type: str
    nullable: bool
    is_pk: bool = False
    is_fk: bool = False
    fk_references: str | None = Field(
        default=None,
        description="Referencia FK con formato 'tabla.columna' o null.",
    )
    functional_definition: str = ""
    observations: str = Field(
        default="",
        description="Notas del agente sobre la columna (ej. razón del tipo, "
        "default aplicado, constraints inusuales).",
    )


class TableAPI(BaseModel):
    """Tabla del modelo expuesta al frontend."""

    table_name: str
    table_description: str = ""
    columns: list[ColumnAPI] = Field(default_factory=list)
    ddl: str = ""


class StandardizedColumnAPI(BaseModel):
    """Cambio de nombre aplicado por QA."""

    table_name: str
    original_name: str
    standardized_name: str
    reason: str = ""


class GuidelineViolationAPI(BaseModel):
    """Violación de lineamiento detectada y corregida por QA."""

    table_name: str
    column_name: str
    violation: str
    correction_applied: str


class NewCatalogEntryAPI(BaseModel):
    """Columna nueva agregada al catálogo corporativo."""

    column_name: str
    functional_definition: str
    data_type: str
    table_name: str


class QAReportAPI(BaseModel):
    """Reporte de QA expuesto al frontend."""

    quality_score: int = Field(ge=0, le=100, default=0)
    standardized_columns: list[StandardizedColumnAPI] = Field(default_factory=list)
    guideline_violations: list[GuidelineViolationAPI] = Field(default_factory=list)
    new_catalog_entries: list[NewCatalogEntryAPI] = Field(default_factory=list)
    summary: str = ""


class ModelingResponseAPI(BaseModel):
    """Respuesta principal de POST /api/conversations/{id}/model."""

    conversation_id: UUIDv4
    engine: str
    turn_number: int = Field(
        ge=1,
        description="Número de turno de la conversación tras este request.",
    )
    tables: list[TableAPI] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)
    export_sql: str = Field(
        default="",
        description="DDL completo de todas las tablas concatenado.",
    )
    export_markdown: str = Field(
        default="",
        description="Modelo completo en formato Markdown.",
    )
    qa_report: QAReportAPI = Field(default_factory=QAReportAPI)
    guidelines_applied: str = Field(
        default="",
        description="Resumen breve de los lineamientos efectivamente aplicados.",
    )
    summary: str = Field(
        default="",
        description="Resumen en lenguaje natural del modelo generado.",
    )


# ─── Health / engines ────────────────────────────────────────────────────


class HealthResponseAPI(BaseModel):
    """Respuesta de GET /api/health."""

    status: str
    version: str
    foundry_connected: bool
    default_engine: str
    active_conversations: int


class EnginesResponseAPI(BaseModel):
    """Respuesta de GET /api/engines."""

    engines: list[str]
    default: str


# ─── Errores ────────────────────────────────────────────────────────────


class ErrorResponseAPI(BaseModel):
    """Estructura uniforme de error de la API."""

    detail: str
    code: str = "error"
