"""Modelos de la feature `changesets` (working copy + flujo de aprobación).

Un changeset es la unidad de versionado/cambio del modelo. Empieza como `draft`
(working copy editable: las lecturas hacen overlay de publicado+changeset), pasa
a `submitted` al crear el *publish request* (se asignan revisores), y termina en
`approved` (se aplica a las colecciones publicadas + cascada) o `rejected`.

Pertenece a **UN proyecto** (`projectId`, doc 75 D2): todo cambio y toda
referencia se resuelven dentro de ese proyecto. `projects` sigue versionado:
renombrar/describir/borrar el proyecto son cambios del draft (doc 75 D5); el
apply de un delete de proyecto cascada en `_apply_and_finalize`.

Los cambios en sí NO viven en el documento del changeset: cada cambio es UN
documento de la colección `changeset_changes` ({@link ChangeDoc}). El viejo dict
embebido `changes` crecía sin techo con ~2-4k entidades
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
    # Procedencia del alta (doc 51, aditivo): {kind: 'paste-table'|'paste-columns'
    # |'ctas', sourceTable?, sourceColumn?}. Solo la muestra el historial de
    # auditoría ("Created from …"); overlay/diff/apply lo ignoran. Un upsert
    # posterior del MISMO draft sin origin lo arrastra (ver set_change).
    origin: dict | None = None
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
    # Doc 75 D2: un changeset pertenece a UN proyecto (lo estampa el servidor).
    projectId: str
    reviewers: list[str] = Field(default_factory=list)    # userIds asignados
    # key = userId · value = {status: 'approved'|'rejected', note?: str, at: str}
    approvals: dict[str, dict] = Field(default_factory=dict)
    comments: list[dict] = Field(default_factory=list)    # {author, text, at}
    # Doc 88 §6: historial de SOLICITUDES de publicación, un registro por ciclo
    # de envío — {id, cycle, submittedAt, submittedBy, title, description,
    # reviewers, outcome: 'pending'|'approved'|'rejected'|'withdrawn',
    # decidedAt?, decidedBy?, note?, decisions?: {user: {status, note?, at}}}.
    # Un rechazo devuelve la versión a `draft` (el owner sigue editando) pero
    # la solicitud, su motivo y sus decisiones quedan acá (Review las lista).
    # Aditivo (invariante §2.6): declarado acá Y en el TS `RequestCycle`.
    requests: list[dict] = Field(default_factory=list)
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
    # Procedencia de una RESTAURACIÓN (doc 65, aditivo): {csId, versionLabel,
    # appliedAt} de la versión publicada que este draft restaura. La estampa
    # `service.rollback`; el UI muestra "Restored from vN" en Home/historial.
    restoredFrom: dict | None = None
    # Doc 104 (aditivo): transferencias de ownership del draft, en orden —
    # {from, to, by, at, note?}. `owner` es siempre el dueño ACTUAL (edita,
    # envía y figura como autor al publicarse); `transfers[0].from` es quien
    # la inició. Invariante §2.6: declarado acá Y en el TS `OwnershipTransfer`.
    transfers: list[dict] = Field(default_factory=list)
    # Doc 104 (ronda 2): un approve cuyo apply falló DESPUÉS de empezar a
    # escribir en producción (parte pudo llegar). Marca de la CABECERA: re-editar
    # el draft no la borra (re-editar reemplaza el documento del cambio y pierde
    # su imagen previa). Mientras exista, el draft no se elimina: se re-envía
    # para completar el publish.
    partialApplyAt: str | None = None
    # Doc 105: una carga Excel ESCRIBIENDO en el draft — {jobId, owner, at,
    # heartbeat} (epoch). Lock de «un apply por versión» visible para los dos
    # procesos de uvicorn; transferir, eliminar y enviar a revisión esperan a
    # que termine. Un latido de más de 10 min = su proceso murió (se ignora).
    uploadLock: dict | None = None
    # Doc 105: un draft de restauración mientras se graban sus inversos. Si el
    # proceso muere en el medio queda en True y `submit` lo rechaza (restore
    # parcial); se elimina y se restaura de nuevo.
    restoreIncomplete: bool | None = None
