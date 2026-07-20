"""Modelos de la feature `changesets` (working copy + flujo de aprobación).

Un changeset es la unidad de versionado/cambio del modelo. Empieza como `draft`
(working copy editable: las lecturas hacen overlay de publicado+changeset), pasa
a `submitted` al crear el *publish request* (se asignan revisores), y termina en
`approved` (se aplica a las colecciones publicadas + cascada) o `rejected`.

Es **cross-project**: un changeset puede tocar tablas de varios proyectos, por
eso lleva `projectIds[]` (chips de proyecto en el UI).

Los cambios en sí NO viven en el documento del changeset: cada cambio es UN
documento de la colección `changeset_changes` ({@link ChangeDoc}). El viejo dict
embebido `changes` topaba el límite de 2MB/doc de Cosmos RU con ~2-4k entidades
tocadas — un changeset grande dejaba de poder guardarse a mitad del trabajo.
Docs legacy con `changes` embebido se ignoran al leer (`extra="ignore"`);
`scripts/migrate_changes_to_collection.py` los migra one-shot.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG


class ChangeDoc(BaseModel):
    """UN cambio de un changeset (doc de `changeset_changes`).

    `id` es determinista — `{csId}::{collection}::{entityId}` — de modo que el
    upsert por `_id` conserva la semántica del viejo dot-path: por entidad gana
    la última escritura, sin poder duplicarse."""

    model_config = DOC_CONFIG

    id: str
    csId: str
    collection: str
    entityId: str
    op: str  # upsert | delete
    payload: dict | None = None
    at: str | None = None  # ISO-8601: baseline de conflicto vs producción
    # Imagen PREVIA de la entidad publicada, capturada en el publish (doc 16
    # §5d): alimenta el rollback (draft inverso). `before=None` con `beforeAt`
    # estampado = la entidad NO existía (el inverso es un delete).
    before: dict | None = None
    beforeAt: str | None = None


class ChangesetDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    owner: str
    status: str = "draft"  # draft | submitted | approved | rejected
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
    # Timestamp de aplicación EXITOSA a las colecciones publicadas. `approved`
    # sin `appliedAt` = el proceso murió entre el claim y el apply (o a mitad):
    # detectable, y recuperable con `scripts/reapply_changeset.py` (el apply es
    # idempotente — upserts por _id).
    appliedAt: str | None = None
