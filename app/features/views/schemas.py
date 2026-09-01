"""DTOs de `views`."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SqlParseBody(BaseModel):
    """Body del sandbox de Query SQL (doc 61): script a validar/interpretar."""
    sql: str = ""


class ViewBody(BaseModel):
    name: str
    sql: str = ""
    description: str | None = None
    # Aditivos del editor de vista (07b-2): viajan por la API para el
    # round-trip. `schema` vía alias (igual que `catalog`).
    tableId: str | None = None
    sql_schema: str | None = Field(default=None, alias="schema")
    tags: list[str] = []
    filter: str | None = None
    # Cada source: {column?, tableId?, outputAlias?, expression?, castType?,
    # description?}. `description` = definición funcional propia de la columna en
    # la vista (F5). list[dict] ⇒ las claves nuevas viajan sin sub-schema.
    sources: list[dict] = []
    outputAlias: str | None = None
    expression: str | None = None
    # ── F3: vistas multi-fuente (contrato compartido F3) ──
    sourceTableIds: list[str] = []
    showOnCanvas: bool = False
    joinOverride: str | None = None
    # ── Doc 61: modo Personalizada + UDPs de vista (espejo de ViewDoc) ──
    customSql: str | None = None
    customColumns: list[dict] = []
    udpValues: dict[str, str] = {}
