"""Orquestación de la carga masiva (doc 55 §3.2, §6-7 · doc 78).

Semántica de guards (misma que `changesets.service.add_change`): `None` = el
changeset no existe; `"forbidden"` = el actor no es el owner; `"locked"` = la
versión ya no está en draft; `"profile-not-found"` = el perfil no existe en
el proyecto de la versión. Los jobs corren como tasks de asyncio y el front
hace polling de `get_job`.

`start_apply` SIEMPRE re-valida contra el estado efectivo fresco (y el perfil
releído) antes de escribir: otro usuario pudo publicar o el owner editar
entre Validate y Upload. Si aparecen errores, el job falla con el reporte
nuevo y no escribe.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from app.features.changesets import service as cs_service
from app.features.changesets.validation import (
    DuplicateEntityError, InvalidPayloadError, NameTooLongError, RelationshipKeyMismatchError,
)

from . import loader
from .jobs import UploadJob, registry
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


def _find(cs_id: str, actor: str, job_id: str) -> UploadJob | str | None:
    job = registry.get(job_id)
    if job is None or job.cs_id != cs_id:
        return None
    if job.owner != actor:
        return "forbidden"
    return job


async def validate_workbook(cs_id: str, body: UploadWorkbookBody,
                            progress: Callable[[str, int, int], None]) -> Plan:
    """Contexto efectivo + perfil + aplicación del perfil (reglas) + planner.
    Compartido por validate y apply. Un perfil borrado entre medio revienta
    con un mensaje legible (el job queda `failed`)."""
    progress("Reading workbook", 1, _VALIDATION_STEPS)
    ctx = await loader.load_context(cs_id)
    progress("Loading profile", 2, _VALIDATION_STEPS)
    profile = await loader.load_profile(ctx.project_id, body.profileId)
    if profile is None:
        raise RuntimeError("Upload profile not found (it may have been deleted): pick another profile and validate again.")
    ctx.profile = profile
    problems = validate_profile(profile, ctx.udp_defs)
    progress("Applying profile and rules", 3, _VALIDATION_STEPS)
    parsed = apply_profile(body, profile, ctx.udp_defs)
    if problems:
        for pr in problems:
            code = "profile-udp-missing" if pr["code"] in _UDP_PROBLEMS else "profile-invalid"
            parsed.issues.append(Issue("error", DEFAULT_SHEET_NAMES[SHEET_PROFILE], None, pr["path"], code,
                                       f"Profile '{profile.get('name')}': {pr['message']} Fix the profile and validate again."))
        parsed.fatal = True
    progress("Loading columns of referenced tables", 4, _VALIDATION_STEPS)
    ctx.columns_by_table = await loader.load_columns(cs_id, referenced_table_ids(parsed, ctx))
    progress("Validating rows", 5, _VALIDATION_STEPS)
    plan = build_plan(parsed, ctx, options=PlanOptions.from_profile(profile))
    progress("Building report", 6, _VALIDATION_STEPS)
    return plan


async def _run_validation(job: UploadJob) -> None:
    try:
        plan = await validate_workbook(job.cs_id, job.body, job.set_progress)
        job.report = plan.report
        job.finish("validated")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — el job reporta el motivo, no revienta el proceso
        log.exception("bulk upload validation failed", extra={"job": job.id, "cs": job.cs_id})
        job.finish("failed", error=str(exc) or type(exc).__name__)


async def _run_apply(job: UploadJob) -> None:
    async with registry.lock_for(job.cs_id):
        try:
            plan = await validate_workbook(job.cs_id, job.body, job.set_progress)
            job.report = plan.report
            if plan.has_errors:
                job.finish("failed", error="Validation found errors after re-checking the current model; "
                                           "nothing was written. Review the report and validate again.")
                return
            batches = [plan.changes[i:i + APPLY_BATCH] for i in range(0, len(plan.changes), APPLY_BATCH)]
            for i, batch in enumerate(batches):
                job.set_progress("Writing changes", i, len(batches))
                res = await cs_service.add_changes_bulk(job.cs_id, job.owner, batch)
                if res == "forbidden":
                    raise RuntimeError("Only the version owner can write its working copy.")
                if res == "locked" or res is None:
                    raise RuntimeError("The version is no longer in draft: it doesn't accept more changes.")
            job.set_progress("Done", len(batches), len(batches))
            job.result = {"affectedCanvasIds": list(plan.affected_canvas_ids), "counts": plan.counts}
            job.finish("applied")
        except asyncio.CancelledError:
            raise
        except (DuplicateEntityError, NameTooLongError, InvalidPayloadError,
                RelationshipKeyMismatchError, RuntimeError) as exc:
            job.finish("failed", error=str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("bulk upload apply failed", extra={"job": job.id, "cs": job.cs_id})
            job.finish("failed", error=str(exc) or type(exc).__name__)


async def start_validation(cs_id: str, actor: str, body: UploadWorkbookBody) -> dict | str | None:
    cs = await _guard(cs_id, actor)
    if not isinstance(cs, dict):
        return cs
    if await loader.load_profile(cs.get("projectId") or "", body.profileId) is None:
        return "profile-not-found"
    job = registry.create(cs_id, actor, body.fileName, body)
    job.task = asyncio.create_task(_run_validation(job))
    return job.view()


async def get_job(cs_id: str, actor: str, job_id: str) -> dict | str | None:
    job = _find(cs_id, actor, job_id)
    return job.view() if isinstance(job, UploadJob) else job


async def start_apply(cs_id: str, actor: str, job_id: str) -> dict | str | None:
    """Además de la semántica de `_guard`: `"not-validated"`, `"has-errors"`,
    `"busy"` (otro apply en curso sobre la misma versión)."""
    job = _find(cs_id, actor, job_id)
    if not isinstance(job, UploadJob):
        return job
    cs = await _guard(cs_id, actor)
    if not isinstance(cs, dict):
        return cs
    if job.status != "validated":
        return "not-validated"
    if (job.report or {}).get("errorCount"):
        return "has-errors"
    if registry.lock_for(cs_id).locked():
        return "busy"
    job.status = "applying"
    job.set_progress("Queued", 0, 0)
    job.task = asyncio.create_task(_run_apply(job))
    return job.view()


async def discard(cs_id: str, actor: str, job_id: str) -> bool | str:
    """`"busy"` si el job está APLICANDO: cancelar la task a mitad dejaría
    tandas escritas sin el resto — se deja terminar (el front bloquea el
    cierre del popup mientras aplica)."""
    job = _find(cs_id, actor, job_id)
    if job == "forbidden":
        return "forbidden"
    if not isinstance(job, UploadJob):
        return False
    if job.status == "applying":
        return "busy"
    registry.discard(job.id)
    return True
