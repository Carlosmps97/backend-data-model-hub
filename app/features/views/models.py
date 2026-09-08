"""Modelo de `views` (vistas SQL)."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, model_validator

from app.core.models import DOC_CONFIG


def normalize_source_tables(data: dict) -> dict:
    """Normaliza el par canónico/legacy de fuentes (contrato F3). Pura.

    - `sourceTableIds` presentes → `tableId` = primera fuente (compat con
      lectores legacy que siguen mirando `tableId`).
    - Solo `tableId` (docs pre-migración) → `sourceTableIds = [tableId]`.
    - Sin fuentes → tal cual (exigir ≥1 fuente es del create, en el router).
    """
    src = data.get("sourceTableIds") or []
    if src:
        return {**data, "tableId": src[0]}
    tid = data.get("tableId")
    if tid:
        return {**data, "sourceTableIds": [tid]}
    return data


class ViewDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Doc 75 D1: alcance por proyecto — obligatorio, lo estampa el servidor.
    projectId: str
    name: str
    sql: str = ""
    description: str | None = None
    # Aditivos del editor de vista (07b-2). Todos opcionales con default
    # NO-breaking (invariante §2.6: declarados ⇒ persisten en el round-trip).
    tableId: str | None = None
    # `schema` es palabra reservada de BaseModel: se persiste/expone como
    # `schema` vía alias (mismo patrón que `catalog`), populate_by_name acepta
    # ambos nombres en la entrada.
    sql_schema: str | None = Field(default=None, alias="schema")
    tags: list[str] = []
    filter: str | None = None
    # Cada source: {column?, tableId?, outputAlias?, expression?, castType?,
    # description?}. `tableId` = de qué fuente viene la columna (F3 multi-fuente);
    # `castType` = override de tipo (el DDL del front emite CAST(...) AS);
    # `description` = definición funcional propia de la columna en la vista (F5,
    # override del origen físico). `sources` es list[dict] → las claves nuevas
    # PERSISTEN sin tocar el modelo (no hay sub-schema estricto).
    sources: list[dict] = []
    outputAlias: str | None = None
    expression: str | None = None
    # ── F3 (10-PRECISIONES §3-5): vistas multi-fuente en canvas ──
    # Canónico de fuentes; `tableId` (arriba) queda = sourceTableIds[0] por
    # compat. Normalización bidireccional en `_normalize_sources`.
    sourceTableIds: list[str] = []
    # D3: flag GLOBAL por vista — aparece en todo canvas con ≥1 fuente.
    # NO afecta el export DDL.
    showOnCanvas: bool = False
    # Condición de JOIN manual (2+ fuentes sin relación modelada, u override
    # de la inferida). El backend NO la valida: la consume el DDL del front.
    joinOverride: str | None = None
    # ── Doc 61: Properties de vista — modo Personalizada + UDPs ──
    # `customSql` NO vacío = vista "Personalizada": el cuerpo del CREATE VIEW
    # ES este script (validado con sqlglot `databricks` al escribir; ver
    # custom_sql.py). Ausente/None = "Regular" (DDL generado de sources/filter/
    # joinOverride, como siempre). NO confundir con `sql` (referencia congelada
    # del preview del editor — no autoritativa).
    customSql: str | None = None
    # Columnas de salida derivadas del parse de `customSql` ({name,
    # expression?}). Las RE-deriva el backend en cada escritura (no se confía
    # en el cliente); alimentan el bloque del canvas y la pestaña Columns.
    customColumns: list[dict] = []
    # Valores UDP de la vista ({defId: value}) — mismo contrato que tablas/
    # columnas/canvas (doc 06); aplican las keys con level='view'.
    udpValues: dict[str, str] = {}

    @model_validator(mode="before")
    @classmethod
    def _normalize_sources(cls, data: object) -> object:
        # Read path (docs Mongo no migrados) Y write path del create quedan
        # normalizados sin tocar a los callers.
        return normalize_source_tables(dict(data)) if isinstance(data, dict) else data
