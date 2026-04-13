"""Modelos Pydantic para las estructuras de datos internas del sistema.

Define los esquemas utilizados para representar tablas, columnas,
modelos de datos, reportes de QA y entradas/salidas del workflow.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


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
