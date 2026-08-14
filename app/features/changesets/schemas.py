"""DTOs de `changesets` (working copy + versiones/requests)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChangesetCreate(BaseModel):
    title: str


class DiffDetailItem(BaseModel):
    """Una entidad puntual del changeset a detallar (popup doc 31)."""
    collection: str
    entityId: str


class DiffDetailsBody(BaseModel):
    """Body de `POST /{cs_id}/diff/details` — lote acotado; el front trocea
    si necesita más de 200 entidades (carga bajo demanda por selección)."""
    items: list[DiffDetailItem] = Field(min_length=1, max_length=200)


class SnapshotBody(BaseModel):
    """Crea un draft (working copy) a partir del estado publicado.
    `title`/`projectIds` opcionales; `versionLabel` se autogenera si no viene."""
    title: str | None = None
    description: str | None = None
    versionLabel: str | None = None
    projectIds: list[str] = []


class ChangeBody(BaseModel):
    collection: str            # parent_domains | glossary_terms | canonical_tables | canonical_columns
    entityId: str
    op: Literal["upsert", "delete"]
    payload: dict[str, Any] | None = None


class ChangesBulkBody(BaseModel):
    """Body de `PUT /{cs_id}/changes/bulk` — lote de cambios en UNA request
    (cascadas: borrar tabla / crear tabla desde fuentes). El front trocea en
    tandas de 1000; el techo es defensivo, no un límite de producto."""
    changes: list[ChangeBody] = Field(min_length=1, max_length=2000)


class ReviewBody(BaseModel):
    """Compat M-series: nota suelta para approve/reject directos."""
    note: str | None = None


class SubmitBody(BaseModel):
    """Publish request: asigna revisores, título y descripción.
    `reviewers=None` (no enviado) PRESERVA los revisores del ciclo anterior;
    con el viejo default `[]` cualquier submit sin body los borraba."""
    title: str | None = None
    description: str | None = None
    reviewers: list[str] | None = None
    projectIds: list[str] | None = None


class ReviewDecisionBody(BaseModel):
    """Decisión de UN revisor asignado (X-Dev-User) sobre el request."""
    decision: Literal["approve", "reject"]
    note: str | None = None


class CommentBody(BaseModel):
    text: str


class SchemaRenameBody(BaseModel):
    """Rename versionado de un esquema (doc 18): nombre nuevo; la propagación
    a tablas/vistas la computa el service server-side dentro del draft."""
    newName: str
