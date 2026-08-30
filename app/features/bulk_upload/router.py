"""Endpoints de la carga masiva (doc 55 §7), bajo el changeset destino:

  POST   /api/changesets/{cs}/uploads                → 202 job en `validating`
  GET    /api/changesets/{cs}/uploads/{job}          → estado / reporte / resultado
  POST   /api/changesets/{cs}/uploads/{job}/apply    → 202 job en `applying`
  DELETE /api/changesets/{cs}/uploads/{job}          → descarta (cancela si sigue validando)

Permiso `model.edit` (como escribir cambios); owner del changeset y draft
se verifican en el service (403 / 409). El body ya viene parseado por hoja
desde el front; el tope de filas se corta acá (413).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.api.envelope import ok
from app.features.auth.deps import require_permission

from . import service
from .jobs import TooManyJobsError
from .schemas import MAX_COLUMN_ROWS, MAX_TABLE_ROWS, UploadWorkbookBody

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
    n_tables = len(body.sheets.tables.rows) if body.sheets.tables else 0
    n_columns = len(body.sheets.columns.rows) if body.sheets.columns else 0
    if n_tables > MAX_TABLE_ROWS or n_columns > MAX_COLUMN_ROWS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"The workbook is too large: up to {MAX_TABLE_ROWS} table rows and "
                   f"{MAX_COLUMN_ROWS} column rows per upload.")
    try:
        res = await service.start_validation(cs_id, user["username"], body)
    except TooManyJobsError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    return ok(_mapped(res))


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
