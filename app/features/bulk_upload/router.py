"""Endpoints de la carga masiva (doc 55 §7), bajo el changeset destino:

  POST   /api/changesets/{cs}/uploads                → 202 job en `validating`
  GET    /api/changesets/{cs}/uploads/targets        → capa de proyectos internos (doc 87)
  GET    /api/changesets/{cs}/uploads/{job}          → estado / reporte / resultado
  POST   /api/changesets/{cs}/uploads/{job}/apply    → 202 job en `applying`
  DELETE /api/changesets/{cs}/uploads/{job}          → descarta (cancela si sigue validando)

Permiso `model.edit` (como escribir cambios); owner del changeset y draft
se verifican en el service (403 / 409); el perfil (`profileId`) debe existir
en el proyecto de la versión (404). El body trae las hojas CRUDAS (doc 78) y
el backend aplica el perfil; los topes se cortan acá (413).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.api.envelope import ok
from app.features.auth.deps import require_permission

from . import service
from .jobs import TooManyJobsError
from .schemas import MAX_CELLS_PER_ROW, MAX_ROWS_PER_SHEET, MAX_SHEETS, UploadWorkbookBody

_can_edit = require_permission("model.edit")

router = APIRouter(prefix="/api/changesets/{cs_id}/uploads", tags=["bulk-upload"])

_GUARD_DETAIL = {
    "forbidden": (status.HTTP_403_FORBIDDEN, "Only the version owner can upload into its working copy."),
    "locked": (status.HTTP_409_CONFLICT,
               "The version is no longer in draft (it was sent to review or closed): it doesn't accept uploads."),
    "not-validated": (status.HTTP_409_CONFLICT, "Validate the workbook before uploading it."),
    "has-errors": (status.HTTP_409_CONFLICT,
                   "The validation report has errors: fix the workbook and validate it again."),
    "busy": (status.HTTP_409_CONFLICT, "Another upload is being applied to this version; wait for it to finish."),
    "profile-not-found": (status.HTTP_404_NOT_FOUND, "Upload profile not found in this project."),
}


def _mapped(res, missing: str = "Version not found."):
    """Convierte la semántica del service (None / códigos) a HTTP; devuelve el dict."""
    if res is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=missing)
    if isinstance(res, str):
        code, detail = _GUARD_DETAIL.get(res, (status.HTTP_409_CONFLICT, res))
        raise HTTPException(status_code=code, detail=detail)
    return res


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def start_validation(cs_id: str, body: UploadWorkbookBody, user: dict = Depends(_can_edit)):
    too_big = (len(body.sheets) > MAX_SHEETS
               or any(len(s.rows) > MAX_ROWS_PER_SHEET for s in body.sheets)
               or any(len(r.cells) > MAX_CELLS_PER_ROW for s in body.sheets for r in s.rows))
    if too_big:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"The workbook is too large: up to {MAX_SHEETS} sheets, {MAX_ROWS_PER_SHEET} rows per sheet "
                   f"and {MAX_CELLS_PER_ROW} cells per row.")
    try:
        res = await service.start_validation(cs_id, user["username"], body)
    except TooManyJobsError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    return ok(_mapped(res))


# Declarada ANTES de `/{job_id}`: FastAPI resuelve en orden de declaración y
# «targets» sería tomado como id de job.
@router.get("/targets")
async def targets(cs_id: str, user: dict = Depends(_can_edit)):
    return ok(_mapped(await service.targets(cs_id, user["username"])))


@router.get("/{job_id}")
async def get_job(cs_id: str, job_id: str, user: dict = Depends(_can_edit)):
    return ok(_mapped(await service.get_job(cs_id, user["username"], job_id),
                      missing="Upload not found (it may have expired): validate the workbook again."))


@router.post("/{job_id}/apply", status_code=status.HTTP_202_ACCEPTED)
async def start_apply(cs_id: str, job_id: str, user: dict = Depends(_can_edit)):
    return ok(_mapped(await service.start_apply(cs_id, user["username"], job_id),
                      missing="Upload not found (it may have expired): validate the workbook again."))


@router.delete("/{job_id}")
async def discard(cs_id: str, job_id: str, user: dict = Depends(_can_edit)):
    res = await service.discard(cs_id, user["username"], job_id)
    if res == "forbidden":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_GUARD_DETAIL["forbidden"][1])
    if res == "busy":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The upload is being applied; it can't be discarded until it finishes.")
    if not res:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found.")
    return ok({"deleted": True})
