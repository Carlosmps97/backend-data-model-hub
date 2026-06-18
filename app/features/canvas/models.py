"""Modelos Pydantic de la feature `canvas` (tablas, columnas, relaciones, vistas).

- **Tables** viven en `project_tables` (shard key `projectId`), con `layer`
  (id de ModelLevel) + `domain` (id de Domain) + `subdomain`, y embeben sus
  vistas SQL por rol en `views[]`.
- **Relationships** viven en `project_relationships` (shard key `projectId`).
- `projectId` NO es campo de estos modelos: se quita en la lectura y se inyecta
  en la escritura en `repository._replace_entities`.
- `schema` se expone como atributo `sql_schema` con `alias="schema"`.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.models import DOC_CONFIG, TagDoc, coerce_tags


class ForeignKeyRefDoc(BaseModel):
    model_config = DOC_CONFIG
    table: str
    column: str


class TableColumnDoc(BaseModel):
    """Una columna. `id` es un identificador estable que sobrevive renames —
    todos los endpoints de relaciones apuntan a él (no a `name`)."""

    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    logicalName: str | None = None
    dataType: str
    length: int | None = None
    scale: int | None = None
    isPrimaryKey: bool | None = None
    isSurrogateKey: bool | None = None
    isPartitionKey: bool | None = None
    isForeignKey: bool | None = None
    # True cuando el usuario fijó la FK a mano — evita que el handler de borrado
    # de relación limpie una FK puesta intencionalmente (Feature 4).
    fkManual: bool | None = None
    foreignKeyRef: ForeignKeyRefDoc | None = None
    isNullable: bool | None = None
    isUnique: bool | None = None
    defaultValue: str | None = None
    functionalDefinition: str | None = None
    observations: str | None = None
    # ── Asignaciones de Semantic Type / UDP (Feature 3) ──────────────────────
    semanticTypeId: str | None = None
    semanticValues: dict[str, str] | None = None  # tag controlado → valor elegido
    udpValues: dict[str, str] | None = None        # udpId → valor
    # ── Metadata de gobierno (Aurora "ESPECIFICACION_FUNCIONAL") ─────────────
    tags: list[TagDoc] | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_legacy_tags(cls, v: Any) -> Any:
        return coerce_tags(v)


class PartitionSpecDoc(BaseModel):
    model_config = DOC_CONFIG

    strategy: str
    columns: list[str] = Field(default_factory=list)
    buckets: int | None = None
    granularity: str | None = None
    clusteringColumns: list[str] | None = None


class NodePositionDoc(BaseModel):
    """Posición de un nodo en el canvas. Persistida vía
    `PATCH /api/projects/{id}/positions`."""

    model_config = DOC_CONFIG

    x: float
    y: float


class ViewDoc(BaseModel):
    """Vista SQL por rol, embebida en el `views[]` de su tabla origen.
    Espeja el TS `View`: `select` multilínea de texto libre y `where`."""

    model_config = DOC_CONFIG

    id: str
    name: str
    type: str = "business"          # 'business' | 'technical'
    role: str | None = None
    select: str = ""
    where: str = ""


class TableDoc(BaseModel):
    """Un documento de `project_tables`. `projectId` NO es campo acá — se quita
    en la lectura y se inyecta en la escritura en `repository._replace_entities`.

    `layer` lleva un id de ModelLevel y `domain` un id de Domain (ambos del
    proyecto padre). `color` se hereda del dominio."""

    model_config = DOC_CONFIG

    id: str
    # La clave de MongoDB sigue siendo "schema"; el atributo Python es "sql_schema".
    sql_schema: str | None = Field(default=None, alias="schema")
    name: str
    logicalName: str | None = None
    layer: str | None = None        # id de ModelLevel
    domain: str | None = None       # id de Domain
    subdomain: str | None = None
    color: str | None = None
    functionalDefinition: str | None = None
    applicationCode: str | None = None
    columns: list[TableColumnDoc] = Field(default_factory=list)
    views: list[ViewDoc] = Field(default_factory=list)
    tags: list[TagDoc] | None = None
    # Asignaciones de UDP a nivel tabla (udpId → valor), Feature 3.
    udpValues: dict[str, str] | None = None
    partition: PartitionSpecDoc | None = None
    # Posición en el canvas persistida por el layout / el drag handler.
    position: NodePositionDoc | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_legacy_tags(cls, v: Any) -> Any:
        return coerce_tags(v)


class RelEndpointDoc(BaseModel):
    """Un extremo de una relación: un par (tableId, columnId)."""

    model_config = DOC_CONFIG
    table: str
    column: str


class RelationshipDoc(BaseModel):
    """Un documento de `project_relationships`. Los endpoints `source`/`target`
    referencian `TableDoc.id` + `TableColumnDoc.id`. `crossLayer`/`skip` se
    derivan en el frontend (no se guardan)."""

    model_config = DOC_CONFIG

    id: str
    source: RelEndpointDoc
    target: RelEndpointDoc
    cardinality: str = "1:N"        # '1:1' | '1:N' | 'N:N'
