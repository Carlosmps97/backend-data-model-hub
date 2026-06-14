"""Pydantic v2 models for every Cosmos DB / MongoDB collection.

These are *persistence-layer* models that mirror the TypeScript interfaces
in `web-data-model-hub/src/types/model.ts` and `types/auth.ts`.

Project-centric architecture (Aurora refactor):
- A **Project** owns `engines[]`, `layers: ModelLevel[]` (the "Modelos / niveles",
  e.g. RDV·UDV·DDV) and `domains: Domain[]` (vertical data products that span
  layers). These small arrays are embedded in the `projects` document.
- **Tables** live in `project_tables` (shard key `projectId`), tagged with
  `layer` (ModelLevel id) + `domain` (Domain id) + `subdomain`, and embed their
  role-specific SQL `views[]`.
- **Relationships** live in `project_relationships` (shard key `projectId`) with
  the nested `source/target/cardinality` shape and may cross layers/domains.

Design rules:
- Field names are camelCase to match Mongo documents.
- `_id` (MongoDB) ↔ `id` (Python/TypeScript): the CRUD helpers map it.
- `projectId` is NOT a field on child models (`TableDoc`, `RelationshipDoc`) —
  it is stripped on read and injected on write by `canvas_db._replace_entities`.
- `schema` is exposed via the `sql_schema` attribute with `alias="schema"`
  (avoids shadowing the deprecated `BaseModel.schema()`).
- `extra="ignore"` so Mongo internal fields (`flgactive`, `deletedAt`, etc.)
  and any legacy fields are dropped silently during validation.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_cfg = ConfigDict(extra="ignore", populate_by_name=True)


# ── Tags ───────────────────────────────────────────────────────────────────

class TagDoc(BaseModel):
    """Key-value metadata tag attached to tables. Mirrors the TS `Tag`
    interface. Both `key` and `value` are free-text. Legacy `list[str]`
    entries are coerced into `{key: "", value: <str>}` by `_coerce_tags`."""

    model_config = _cfg

    key: str = ""
    value: str = ""


def _coerce_tags(raw: Any) -> Any:
    """Accept either the new `list[TagDoc]` shape or the legacy `list[str]`
    shape from pre-migration Cosmos documents."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        return raw  # let Pydantic raise its usual validation error
    out: list[Any] = []
    for item in raw:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                out.append({"key": "", "value": stripped})
        else:
            out.append(item)
    return out


# ── Project-level catalog: levels (models) + domains ────────────────────────

class ModelLevelDoc(BaseModel):
    """A "Modelo (nivel)" — agnostic layer of the project (e.g. RDV/UDV/DDV).
    Mirrors the TS `ModelLevel`. Embedded in the project document."""

    model_config = _cfg

    id: str
    name: str                       # short code, e.g. "RDV"
    full: str | None = None         # full name, e.g. "Raw Data Vault"
    color: str | None = None
    order: int = 0                  # left→right canvas position + adjacency
    engine: str | None = None
    desc: str | None = None


class DomainDoc(BaseModel):
    """A Domain (data product) — project-level and VERTICAL (spans layers).
    Mirrors the TS `Domain`. Embedded in the project document."""

    model_config = _cfg

    id: str
    name: str
    color: str | None = None
    owner: str | None = None
    steward: str | None = None
    # 'Public' | 'Internal' | 'Confidential' | 'Restricted' | 'PII' — kept as a
    # plain str for resilience against legacy / agent-written values.
    sensitivity: str | None = None
    description: str | None = None
    subdomains: list[str] = Field(default_factory=list)
    layers: list[str] = Field(default_factory=list)  # ModelLevel ids spanned


# ── Project ────────────────────────────────────────────────────────────────

class ProjectDoc(BaseModel):
    model_config = _cfg

    id: str
    name: str
    description: str | None = None
    engines: list[str] = Field(default_factory=list)
    layers: list[ModelLevelDoc] = Field(default_factory=list)
    domains: list[DomainDoc] = Field(default_factory=list)
    createdAt: str
    updatedAt: str


# ── Shared nested types ────────────────────────────────────────────────────

class ForeignKeyRefDoc(BaseModel):
    model_config = _cfg
    table: str
    column: str


class TableColumnDoc(BaseModel):
    """One column. `id` is a stable identifier that survives renames — all
    relationship endpoints point at it (not at `name`)."""

    model_config = _cfg

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


class NodePositionDoc(BaseModel):
    """Canvas position of a node. Persisted via `PATCH /api/projects/{id}/positions`."""

    model_config = _cfg

    x: float
    y: float


# ── Views (role-specific SQL projection, embedded in a table) ───────────────

class ViewDoc(BaseModel):
    """A role-specific SQL view, embedded in its source table's `views[]`.
    Mirrors the TS `View`: free-text multiline `select` (one expression per
    line) and `where` (AND-joined conditions)."""

    model_config = _cfg

    id: str
    name: str
    type: str = "business"          # 'business' | 'technical'
    role: str | None = None
    select: str = ""
    where: str = ""


# ── project_tables ──────────────────────────────────────────────────────────

class TableDoc(BaseModel):
    """One document from `project_tables`. `projectId` is NOT a field here —
    it is stripped on read and injected on write by `canvas_db._replace_entities`.

    `layer` holds a ModelLevel id and `domain` holds a Domain id (both refer to
    the parent project's embedded `layers[]` / `domains[]`). `color` is inherited
    from the domain."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    # MongoDB key stays "schema"; Python attribute is "sql_schema".
    sql_schema: str | None = Field(default=None, alias="schema")
    name: str
    logicalName: str | None = None
    layer: str | None = None        # ModelLevel id
    domain: str | None = None       # Domain id
    subdomain: str | None = None
    color: str | None = None
    functionalDefinition: str | None = None
    applicationCode: str | None = None
    columns: list[TableColumnDoc] = Field(default_factory=list)
    views: list[ViewDoc] = Field(default_factory=list)
    tags: list[TagDoc] | None = None
    partition: PartitionSpecDoc | None = None
    # Canvas position persisted by the ELK layout engine / drag handler.
    position: NodePositionDoc | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_legacy_tags(cls, v: Any) -> Any:
        return _coerce_tags(v)


# ── project_relationships ───────────────────────────────────────────────────

class RelEndpointDoc(BaseModel):
    """One end of a relationship: a (tableId, columnId) pair."""

    model_config = _cfg
    table: str
    column: str


class RelationshipDoc(BaseModel):
    """One document from `project_relationships`. Nested `source`/`target`
    endpoints reference `TableDoc.id` + `TableColumnDoc.id`. May cross layers
    and domains; `crossLayer`/`skip` are derived on the frontend (not stored)."""

    model_config = _cfg

    id: str
    source: RelEndpointDoc
    target: RelEndpointDoc
    cardinality: str = "1:N"        # '1:1' | '1:N' | 'N:N'


# ── users ──────────────────────────────────────────────────────────────────

class PermissionDoc(BaseModel):
    """Project-scoped grant. The legacy `scope`/`modelId` fields (model-scope)
    are dropped — `extra="ignore"` keeps pre-migration docs loading."""

    model_config = _cfg

    projectId: str
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
