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


def normalize_custom_sql(data: dict) -> dict:
    """Doc 91 D6 → doc 96 D6: el User-Defined SQL (texto informativo) se guarda
    VERBATIM, sin parse ni validación; única normalización = `strip()`, y
    vacío ⇒ None (sin texto). Pura."""
    sql = (data.get("customSql") or "").strip()
    return {**data, "customSql": sql or None}


class ViewDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Doc 75 D1: alcance por proyecto — obligatorio, lo estampa el servidor.
    projectId: str
    name: str
    # Referencia CONGELADA (no autoritativa): el preview del editor al guardar
    # o el `ViewProps.SQL` original de Erwin (doc 19 §12). El DDL se genera
    # SIEMPRE desde `sources` (doc 96 D6).
    sql: str = ""
    description: str | None = None
    # Aditivos del editor de vista (07b-2). Todos opcionales con default
    # NO-breaking (invariante §2.6: declarados ⇒ persisten en el round-trip).
    tableId: str | None = None
    # `schema` es palabra reservada de BaseModel: se persiste/expone como
    # `schema` vía alias (mismo patrón que `catalog`), populate_by_name acepta
    # ambos nombres en la entrada.
    sql_schema: str | None = Field(default=None, alias="schema")
    # Doc 91 D1/D2/D5: `tags`, `filter` y `joinOverride` se RETIRARON del
    # modelo (extra="ignore" descarta los valores legacy al leer; desaparecen
    # al próximo write). Los UDP de vista reemplazan a las etiquetas; toda
    # personalización (WHERE, JOIN) vive en el User-Defined SQL.
    # Cada source: {column?, tableId?, outputAlias?, expression?, castType?,
    # description?}. `tableId` = de qué fuente viene la columna (F3 multi-fuente);
    # `castType` = override de tipo (el DDL del front emite CAST(...) AS);
    # `description` = definición funcional propia de la columna en la vista (F5,
    # override del origen físico). `sources` es list[dict] → las claves nuevas
    # PERSISTEN sin tocar el modelo (no hay sub-schema estricto).
    # Doc 96 D6: las columnas de salida son siempre `sources` (la proyección
    # del editor): de acá sale el DDL y lo que pinta el canvas.
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
    # ── Doc 61 → 91 → doc 96 D6: «User-Defined SQL», texto INFORMATIVO ──
    # Se guarda tal cual, sin parse ni validación (p. ej. el `User_Defined_SQL`
    # de Erwin o el JOIN/WHERE que el modelador anota) y NO se exporta: el DDL
    # se genera de `sources`. None = sin texto. No confundir con `sql`.
    customSql: str | None = None
    # Valores UDP de la vista ({defId: value}) — mismo contrato que tablas/
    # columnas/canvas (doc 06); aplican las keys con level='view'.
    udpValues: dict[str, str] = {}

    @model_validator(mode="before")
    @classmethod
    def _normalize_sources(cls, data: object) -> object:
        # Read path (docs Mongo no migrados) Y write path del create quedan
        # normalizados sin tocar a los callers.
        return normalize_source_tables(dict(data)) if isinstance(data, dict) else data
