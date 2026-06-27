"""Modelo de la feature `changesets` (working copy + flujo de aprobación).

Un changeset es la unidad de versionado/cambio del modelo. Empieza como `draft`
(working copy editable: las lecturas hacen overlay de publicado+changeset), pasa
a `submitted` al crear el *publish request* (se asignan revisores), y termina en
`approved` (se aplica a las colecciones publicadas + cascada) o `rejected`.

Es **cross-project**: un changeset puede tocar tablas de varios proyectos, por
eso lleva `projectIds[]` (chips de proyecto en el UI).

Campos nuevos (R1b, aditivos con default — invariante §2.6):
- `description`, `versionLabel`, `projectIds[]`, `reviewers[]`,
  `approvals{userId:{status,note?,at}}`, `comments[{author,text,at}]`.
Se conservan `status`, `owner`, `changes`, `reviewedBy/At/Note` (compat M-series).
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG


class ChangesetDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    owner: str
    status: str = "draft"  # draft | submitted | approved | rejected
    changes: dict = Field(default_factory=dict)
    # ── Versionado / publish request (R1b, aditivos) ──────────────────────
    description: str | None = None
    versionLabel: str | None = None          # p.ej. "v15" (autoincremental)
    projectIds: list[str] = Field(default_factory=list)   # cross-project chips
    reviewers: list[str] = Field(default_factory=list)    # userIds asignados
    # key = userId · value = {status: 'approved'|'rejected', note?: str, at: str}
    approvals: dict[str, dict] = Field(default_factory=dict)
    comments: list[dict] = Field(default_factory=list)    # {author, text, at}
    # ── Timestamps / compat de revisión single (M-series) ─────────────────
    createdAt: str | None = None
    updatedAt: str | None = None
    submittedAt: str | None = None
    reviewedBy: str | None = None
    reviewedAt: str | None = None
    reviewNote: str | None = None
