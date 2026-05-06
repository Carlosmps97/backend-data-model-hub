"""Pydantic v2 models for every Cosmos DB / MongoDB collection.

These are *persistence-layer* models that mirror the TypeScript interfaces
in `web-data-model-hub/src/types/model.ts` and `types/auth.ts`.

Design rules:
- Field names are camelCase to match Mongo documents.
- `_id` (MongoDB) ↔ `id` (Python/TypeScript): the CRUD helpers perform the
  mapping; these models always use `id`.
- `modelId` is NOT present on child models (`TableModelDoc`, `RelationshipDoc`,
  `ViewModelDoc`) because it is stripped on read and injected on write, exactly
  as the TypeScript layer does.
- `schema` is a valid Pydantic v2 field name (not a reserved keyword).
- `extra="ignore"` so Mongo internal fields (`flgactive`, `deletedAt`, etc.)
  are dropped silently during validation.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


_cfg = ConfigDict(extra="ignore", populate_by_name=True)


# ── Project ────────────────────────────────────────────────────────────────

class ProjectDoc(BaseModel):
    model_config = _cfg

    id: str
    name: str
    description: str | None = None
    createdAt: str
    updatedAt: str


# ── Shared nested types ────────────────────────────────────────────────────

class ForeignKeyRefDoc(BaseModel):
    model_config = _cfg
    table: str
    column: str


class TableColumnDoc(BaseModel):
    model_config = _cfg

    # Stable identifier that survives renames. Mirrors the TS
    # `TableColumn.id` — all `RelationshipDoc.sourceColumn` /
    # `targetColumn` point at this value (not at `name`).
    # Default factory makes in-memory construction safe; persistence
    # layers should pass an explicit id when the document already
    # carries one, to keep it stable across saves.
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
    foreignKeyRef: ForeignKeyRefDoc | None = None
    isNullable: bool | None = None
    isUnique: bool | None = None
    defaultValue: str | None = None
    functionalDefinition: str | None = None
    observations: str | None = None


class PartitionSpecDoc(BaseModel):
    model_config = _cfg

    strategy: str
    columns: list[str] = Field(default_factory=list)
    buckets: int | None = None
    granularity: str | None = None
    clusteringColumns: list[str] | None = None


class ViewColumnTransformDoc(BaseModel):
    model_config = _cfg

    sourceColumn: str
    alias: str | None = None
    expression: str | None = None
    include: bool = True


class SubdomainDefinitionDoc(BaseModel):
    model_config = _cfg

    id: str
    name: str
    code: str | None = None
    description: str | None = None
    defaults: dict[str, Any] | None = None


class DomainDefinitionDoc(BaseModel):
    model_config = _cfg

    id: str
    name: str
    code: str | None = None
    description: str | None = None
    color: str | None = None
    subdomains: list[SubdomainDefinitionDoc] = Field(default_factory=list)
    defaults: dict[str, Any] | None = None


# ── model_tables ───────────────────────────────────────────────────────────

class TableModelDoc(BaseModel):
    """One document from `model_tables`. `modelId` is NOT a field here —
    it is stripped on read and injected on write by `models_db._replace_entities`."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    # "schema" shadows deprecated BaseModel.schema() — use alias so the MongoDB
    # key stays "schema" while the Python attribute is "sql_schema".
    sql_schema: str | None = Field(default=None, alias="schema")
    name: str
    logicalName: str | None = None
    columns: list[TableColumnDoc] = Field(default_factory=list)
    functionalDefinition: str | None = None
    color: str | None = None
    applicationCode: str | None = None
    domain: str | None = None
    subdomain: str | None = None
    domainId: str | None = None
    subdomainId: str | None = None
    tags: list[str] | None = None
    partition: PartitionSpecDoc | None = None


# ── model_relationships ────────────────────────────────────────────────────

class RelationshipDoc(BaseModel):
    model_config = _cfg

    id: str
    sourceTable: str
    sourceColumn: str
    targetTable: str
    targetColumn: str
    type: str  # 'one-to-one' | 'one-to-many' | 'many-to-many'


# ── model_views ────────────────────────────────────────────────────────────

class ViewModelDoc(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    name: str
    logicalName: str | None = None
    sql_schema: str | None = Field(default=None, alias="schema")
    sourceTableId: str
    viewType: str  # 'technical' | 'user' | 'business'
    columns: list[ViewColumnTransformDoc] = Field(default_factory=list)
    whereClause: str | None = None
    useCustomSql: bool | None = None
    customSql: str | None = None
    functionalDefinition: str | None = None
    domain: str | None = None
    subdomain: str | None = None
    domainId: str | None = None
    subdomainId: str | None = None
    tags: list[str] | None = None
    color: str | None = None


# ── models (metadata + embedded domainCatalog) ─────────────────────────────

class DataModelDoc(BaseModel):
    """Full DataModel with child entities hydrated.

    When reading from the `models` collection the child arrays start empty
    and are populated from `model_tables`, `model_relationships`, and
    `model_views` by the CRUD layer (same architecture as TypeScript).
    """

    model_config = _cfg

    id: str
    projectId: str
    name: str
    description: str | None = None
    engine: str | None = None
    tables: list[TableModelDoc] = Field(default_factory=list)
    relationships: list[RelationshipDoc] = Field(default_factory=list)
    views: list[ViewModelDoc] = Field(default_factory=list)
    domainCatalog: list[DomainDefinitionDoc] = Field(default_factory=list)
    createdAt: str
    updatedAt: str


# ── users ──────────────────────────────────────────────────────────────────

class PermissionDoc(BaseModel):
    model_config = _cfg

    scope: Literal["project", "model"]
    projectId: str
    modelId: str | None = None  # present only when scope == "model"
    level: Literal["view", "edit"]


class UserDoc(BaseModel):
    model_config = _cfg

    id: str
    username: str
    passwordHash: str
    role: Literal["admin", "editor", "viewer"]
    isActive: bool
    permissions: list[PermissionDoc] = Field(default_factory=list)
    createdAt: str
    statusUpdatedAt: str
    lastLoginAt: str | None = None
