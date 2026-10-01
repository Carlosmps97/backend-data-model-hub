"""Orquestación de la carga masiva (doc 55 §3.2, §6-7 · doc 78 · doc 105).

Semántica de guards (misma que `changesets.service.add_change`): `None` = el
changeset no existe; `"forbidden"` = el actor no es el owner; `"locked"` = la
versión ya no está en draft; `"profile-not-found"` = el perfil no existe en
el proyecto de la versión. Los jobs corren como tasks de asyncio y el front
hace polling de `get_job`.

Doc 105 — `uvicorn --workers 2`: crear, consultar, aplicar y descartar pueden
caer en procesos distintos. El ESTADO del job (avance, reporte, resultado y
las hojas crudas) vive en la BD (`jobs.JobStore`); lo único local es la task,
en el proceso que la lanzó. El lock de «un apply por versión» es `uploadLock`
en la cabecera del changeset (`changesets.repository.claim_upload_lock`), con
latido por paso y por tanda: transferir, eliminar y enviar a revisión lo
respetan en su misma sentencia.

`start_apply` SIEMPRE re-valida contra el estado efectivo fresco (y el perfil
releído) antes de escribir: otro usuario pudo publicar o el owner editar
entre Validate y Upload. Si aparecen errores, el job falla con el reporte
nuevo y no escribe.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Coroutine

from app.features.changesets import repository as cs_repository
from app.features.changesets import service as cs_service
from app.features.changesets.validation import (
    DuplicateEntityError, InvalidPayloadError, NameTooLongError, RelationshipKeyMismatchError,
)

from . import loader
from .jobs import store
from .plan_structure import upload_targets
from .planner import Plan, PlanOptions, build_plan, referenced_table_ids
from .profiles.apply import apply_profile
from .profiles.models import validate_profile
from .report import DEFAULT_SHEET_NAMES, SHEET_PROFILE, Issue
from .schemas import UploadWorkbookBody

log = logging.getLogger(__name__)

# Tanda por request al bulk (mismo criterio que el front, doc 39).
APPLY_BATCH = 1000
_VALIDATION_STEPS = 6
_UDP_PROBLEMS = frozenset({"udp-unknown", "udp-wrong-level"})


async def _guard(cs_id: str, actor: str) -> dict | str | None:
    cs = await cs_service.get(cs_id)
    if not cs:
        return None
    if cs.get("owner") != actor:
        return "forbidden"
    if cs.get("status") != "draft":
        return "locked"
    return cs


# Tasks EN ESTE proceso (referencia fuerte: asyncio sólo guarda débiles; y
# permite cancelar una validación que se descarta en el mismo proceso).
_TASKS: dict[str, asyncio.Task] = {}


def _spawn(job_id: str, coro: Coroutine) -> None:
    task = asyncio.create_task(coro)
    _TASKS[job_id] = task
    task.add_done_callback(lambda t: _TASKS.pop(job_id, None) if _TASKS.get(job_id) is t else None)


async def _find(cs_id: str, actor: str, job_id: str) -> dict | str | None:
    job = await store.get(job_id)
    if job is None or job.get("csId") != cs_id:
        return None
    if job.get("owner") != actor:
        return "forbidden"
    return job


async def validate_workbook(cs_id: str, body: UploadWorkbookBody,
                            progress: Callable[[str, int, int], Awaitable[None]]) -> Plan:
    """Contexto efectivo + perfil + aplicación del perfil (reglas) + planner.
    Compartido por validate y apply. Un perfil borrado entre medio revienta
    con un mensaje legible (el job queda `failed`)."""
    await progress("Reading workbook", 1, _VALIDATION_STEPS)
    ctx = await loader.load_context(cs_id)
    await progress("Loading profile", 2, _VALIDATION_STEPS)
    profile = await loader.load_profile(ctx.project_id, body.profileId)
    if profile is None:
        raise RuntimeError("Upload profile not found (it may have been deleted): pick another profile and validate again.")
    ctx.profile = profile
    problems = validate_profile(profile, ctx.udp_defs)
    await progress("Applying profile and rules", 3, _VALIDATION_STEPS)
    parsed = apply_profile(body, profile, ctx.udp_defs)
    for pr in problems:
        if pr["code"] == "default-invalid":
            # Doc 105 (ronda 7): un default que QUEDÓ inválido (p. ej. Data
            # Standards sacó el valor de la lista) no bloquea la carga — aviso; la
            # fila que lo usaría da su error. Al guardar el perfil sigue siendo 422.
            parsed.issues.append(Issue("warning", DEFAULT_SHEET_NAMES[SHEET_PROFILE], None, pr["path"],
                                       "profile-default-invalid",
                                       f"Profile '{profile.get('name')}': {pr['message']} Rows that leave this cell "
                                       "empty will fail; fix the profile."))
            continue
        code = "profile-udp-missing" if pr["code"] in _UDP_PROBLEMS else "profile-invalid"
        parsed.issues.append(Issue("error", DEFAULT_SHEET_NAMES[SHEET_PROFILE], None, pr["path"], code,
                                   f"Profile '{profile.get('name')}': {pr['message']} Fix the profile and validate again."))
        parsed.fatal = True
    await progress("Loading columns of referenced tables", 4, _VALIDATION_STEPS)
    ctx.columns_by_table = await loader.load_columns(cs_id, referenced_table_ids(parsed, ctx))
    await progress("Validating rows", 5, _VALIDATION_STEPS)
    plan = build_plan(parsed, ctx, options=PlanOptions.from_profile(profile), target_folder_id=body.targetFolderId)
    await progress("Building report", 6, _VALIDATION_STEPS)
    return plan


async def _run_validation(job_id: str, cs_id: str, body: UploadWorkbookBody) -> None:
    async def progress(phase: str, done: int, total: int) -> None:
        await store.progress(job_id, phase, done, total)

    try:
        plan = await validate_workbook(cs_id, body, progress)
        await store.finish(job_id, "validated", report=plan.report)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — el job reporta el motivo, no revienta el proceso
        log.exception("bulk upload validation failed", extra={"job": job_id, "cs": cs_id})
        await store.finish(job_id, "failed", error=str(exc) or type(exc).__name__)


async def _run_apply(job_id: str, cs_id: str, owner: str) -> None:
    """Corre con el lock `uploadLock` de la versión ya tomado (start_apply) y
    lo suelta al terminar, pase lo que pase. Cada paso y cada tanda son
    también su latido."""
    async def progress(phase: str, done: int, total: int) -> None:
        await store.progress(job_id, phase, done, total)
        if not await cs_repository.heartbeat_upload_lock(cs_id, job_id):
            # Doc 105 (R1): el lock ya no es de este job — la versión se eliminó,
            # o un paso tardó más que el vencimiento y otra carga la tomó. Seguir
            # escribiría a la par de esa otra carga.
            writing = phase == "Writing changes"
            gone = await cs_repository.get(cs_id) is None
            raise RuntimeError(_interrupted(None if gone else "lock-lost",
                                            done if writing else 0, total if writing else 0))

    try:
        body = await store.load_body(job_id)
        if body is None:
            raise RuntimeError("Upload not found (it may have expired): validate the workbook again.")
        plan = await validate_workbook(cs_id, body, progress)
        if plan.has_errors:
            await store.finish(job_id, "failed", report=plan.report,
                               error="Validation found errors after re-checking the current model; "
                                     "nothing was written. Review the report and validate again.")
            return
        batches = [plan.changes[i:i + APPLY_BATCH] for i in range(0, len(plan.changes), APPLY_BATCH)]
        for i, batch in enumerate(batches):
            await progress("Writing changes", i, len(batches))
            res = await cs_service.add_changes_bulk(cs_id, owner, batch)
            if res is None or isinstance(res, str):
                raise RuntimeError(_interrupted(res, i, len(batches)))
        await store.progress(job_id, "Done", len(batches), len(batches))
        await store.finish(job_id, "applied", report=plan.report,
                           result={"affectedCanvasIds": list(plan.affected_canvas_ids), "counts": plan.counts})
    except asyncio.CancelledError:
        raise
    except (DuplicateEntityError, NameTooLongError, InvalidPayloadError,
            RelationshipKeyMismatchError, RuntimeError) as exc:
        await store.finish(job_id, "failed", error=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("bulk upload apply failed", extra={"job": job_id, "cs": cs_id})
        await store.finish(job_id, "failed", error=str(exc) or type(exc).__name__)
    finally:
        await cs_repository.release_upload_lock(cs_id, job_id)


def _interrupted(res: str | None, written: int, total: int) -> str:
    """Motivo legible de una carga que se cortó a mitad: doc 104 — la versión
    puede transferirse o eliminarse mientras se escribe. Dice cuántas tandas
    alcanzaron a grabarse (quedan en la versión)."""
    if res is None:
        # Eliminar la versión borra también lo que la carga alcanzó a escribir.
        return "The version was deleted, so the upload stopped: nothing of it is kept."
    if res == "forbidden":
        why = "The version now belongs to another user (it was transferred)"
    elif res == "lock-lost":
        why = "Another upload took over this version (this one had stopped responding)"
    else:
        why = "The version is no longer in draft: it doesn't accept more changes"
    saved = (f" {written} of {total} batches had already been saved in it." if written
             else " Nothing was saved.")
    return f"{why}, so the upload stopped.{saved}"


async def targets(cs_id: str, actor: str) -> dict | str | None:
    """Doc 87 §3.5: capa de proyectos internos del proyecto de la versión
    (carpetas EFECTIVAS del draft) para el selector del popup. Mismos guards
    que subir (owner + draft)."""
    cs = await _guard(cs_id, actor)
    if not isinstance(cs, dict):
        return cs
    return upload_targets(await loader.load_folders(cs_id))


async def start_validation(cs_id: str, actor: str, body: UploadWorkbookBody) -> dict | str | None:
    cs = await _guard(cs_id, actor)
    if not isinstance(cs, dict):
        return cs
    if await loader.load_profile(cs.get("projectId") or "", body.profileId) is None:
        return "profile-not-found"
    job = await store.create(cs_id, actor, body.fileName, body)
    _spawn(job["_id"], _run_validation(job["_id"], cs_id, body))
    return store.view(job)


async def get_job(cs_id: str, actor: str, job_id: str) -> dict | str | None:
    job = await _find(cs_id, actor, job_id)
    return store.view(job) if isinstance(job, dict) else job


async def start_apply(cs_id: str, actor: str, job_id: str) -> dict | str | None:
    """Además de la semántica de `_guard`: `"not-validated"`, `"has-errors"`,
    `"busy"` (otra carga escribiendo en la misma versión, en cualquier proceso)."""
    job = await _find(cs_id, actor, job_id)
    if not isinstance(job, dict):
        return job
    cs = await _guard(cs_id, actor)
    if not isinstance(cs, dict):
        return cs
    if job.get("status") != "validated":
        return "not-validated"
    if (job.get("report") or {}).get("errorCount"):
        return "has-errors"
    if cs_repository.upload_lock_active(cs):
        return "busy"
    claimed = await store.claim_apply(job_id)          # dos clics / dos procesos: uno solo
    if claimed is None:
        return "not-validated"
    if await cs_repository.claim_upload_lock(cs_id, actor, job_id) is None:
        await store.unclaim_apply(job_id)
        fresh = await cs_service.get(cs_id)            # ¿por qué no? (cambió en el medio)
        if not fresh:
            return None
        if fresh.get("owner") != actor:
            return "forbidden"
        if fresh.get("status") != "draft":
            return "locked"
        return "busy"
    _spawn(job_id, _run_apply(job_id, cs_id, actor))
    return store.view(claimed)


async def discard(cs_id: str, actor: str, job_id: str, *, only_status: str | None = None) -> bool | str:
    """`"busy"` si el job está APLICANDO: cancelar la task a mitad dejaría
    tandas escritas sin el resto — se deja terminar (el front bloquea el
    cierre del popup mientras aplica). Una validación en curso EN ESTE proceso
    se cancela; si corre en el otro, termina sola y su escritura tardía no
    revive el job (sin upsert)."""
    job = await _find(cs_id, actor, job_id)
    if job == "forbidden":
        return "forbidden"
    if not isinstance(job, dict):
        return False
    res = await store.discard(job_id, only_status=only_status)
    if res is True:
        task = _TASKS.pop(job_id, None)
        if task is not None and not task.done():
            task.cancel()
    return res
