"""DTOs de los endpoints stateless de DDL Export Rules (bench del editor)."""
from __future__ import annotations

from pydantic import BaseModel

from app.features.data_standards.schemas import DdlRuleEdit


class TestBody(BaseModel):
    """Probar una regla contra una tabla REAL del catálogo (spec 15.4)."""
    rule: DdlRuleEdit
    tableId: str


class ImpactBody(BaseModel):
    """Estimar alcance: '43 columns across 12 tables' (pantalla 16c)."""
    rule: DdlRuleEdit


class RenderTableEntry(BaseModel):
    """Una tabla del export: doc canónico + columnas + su CREATE base (el front
    genera el DDL base con el estado EFECTIVO — drafts incluidos)."""
    table: dict
    columns: list[dict] = []
    baseSql: str = ""


class RenderViewEntry(BaseModel):
    name: str
    schema_: str | None = None
    sql: str
    sourceTableIds: list[str] = []
    # Vista de negocio = "on canvas" (pedido owner 07-20): solo esas se decoran
    # con ddl.vista_negocio; el resto pasa intacto al archivo.
    businessView: bool = True

    model_config = {"populate_by_name": True}

    def __init__(self, **data):  # acepta 'schema' del payload JSON
        if "schema" in data and "schema_" not in data:
            data["schema_"] = data.pop("schema")
        super().__init__(**data)


class RenderBody(BaseModel):
    """Contrato del puente export (doc 30 §8): el motor es PURO — transforma lo
    que el front manda y no lee el modelo."""
    ruleIds: list[str]
    model: dict | None = None            # {name, udpValues} del canvas
    tables: list[RenderTableEntry] = []
    views: list[RenderViewEntry] = []
    # Opciones del modal de export (identifierCase/tableFormat/external/
    # location/includePartitions): los artefactos generados salen espejo del
    # CREATE físico (pedido owner 07-20).
    options: dict = {}
