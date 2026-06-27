"""DTOs de `changesets` (working copy + versiones/requests)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class ChangesetCreate(BaseModel):
    title: str


class SnapshotBody(BaseModel):
    """Crea un draft (working copy) a partir del estado publicado.
    `title`/`projectIds` opcionales; `versionLabel` se autogenera si no viene."""
    title: str | None = None
    description: str | None = None
    versionLabel: str | None = None
    projectIds: list[str] = []


class ChangeBody(BaseModel):
    collection: str            # parent_domains | abbreviation_dict | canonical_tables | canonical_columns
    entityId: str
    op: Literal["upsert", "delete"]
    payload: dict[str, Any] | None = None


class ReviewBody(BaseModel):
    """Compat M-series: nota suelta para approve/reject directos."""
    note: str | None = None


class SubmitBody(BaseModel):
    """Publish request: asigna revisores, título y descripción."""
    title: str | None = None
    description: str | None = None
    reviewers: list[str] = []
    projectIds: list[str] | None = None


class ReviewDecisionBody(BaseModel):
    """Decisión de UN revisor asignado (X-Dev-User) sobre el request."""
    decision: Literal["approve", "reject"]
    note: str | None = None


class CommentBody(BaseModel):
    text: str
