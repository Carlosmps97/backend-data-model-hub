"""Endpoint de import de Excel.

POST /api/excel-import/preview — recibe un .xlsx por multipart/form-data y
devuelve una previsualización ya normalizada. No persiste nada — el frontend
ajusta y luego dispara el PUT habitual del canvas.
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.core.api.envelope import ok
from app.core.logging import get_logger
from app.features.auth import AuthUserDep

from .service import parse_workbook

log = get_logger("api.excel_import")

router = APIRouter(prefix="/api/excel-import", tags=["excel-import"])

#: Tope defensivo para uploads (10 MB ≈ decenas de miles de filas).
_MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024
_ALLOWED_EXTENSIONS: tuple[str, ...] = (".xlsx", ".xlsm")


@router.post("/preview")
async def preview_excel_import(
    user: AuthUserDep,
    file: UploadFile = File(...),
):
    """Devuelve la previsualización del workbook subido."""
    _ensure_valid_filename(file.filename)
    payload = await _read_upload(file)

    try:
        preview = parse_workbook(payload)
    except Exception as exc:
        log.exception("excel parse failed", extra={"filename": file.filename})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not parse Excel file: {exc}",
        ) from exc

    return ok(preview.model_dump(by_alias=True))


# ─── Helpers ───────────────────────────────────────────────────────────────


def _ensure_valid_filename(filename: str | None) -> None:
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No filename provided.",
        )
    if not filename.lower().endswith(_ALLOWED_EXTENSIONS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type. Expected one of: {', '.join(_ALLOWED_EXTENSIONS)}.",
        )


async def _read_upload(file: UploadFile) -> bytes:
    """Lee el upload entero a memoria con un cap en bytes."""
    payload = await file.read()
    if len(payload) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Limit is {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file.",
        )
    return payload
