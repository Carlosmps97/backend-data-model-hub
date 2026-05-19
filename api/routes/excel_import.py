"""Excel-import endpoints.

POST /api/excel-import/preview — recibe un .xlsx por multipart/form-data,
devuelve una previsualización ya normalizada (tipos canónicos,
schema/tabla separados, descripciones de tabla unificadas).

El parsing entero vive en `src/excel_import/*`. Este router solo se
ocupa de:
- Validar que el archivo sea .xlsx y no exceda el límite (10 MB).
- Llamar al servicio.
- Envolver la respuesta en `ok(...)`.
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from src.api.dependencies import AuthUserDep
from src.api.response_builder import ok
from src.excel_import import parse_workbook
from src.logger import get_logger

log = get_logger("api.excel_import")

router = APIRouter(prefix="/api/excel-import", tags=["excel-import"])

#: Tope defensivo para uploads. Un xlsx con 10 MB ya implica decenas de
#: miles de filas — más que cualquier modelo razonable.
_MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024

_ALLOWED_EXTENSIONS: tuple[str, ...] = (".xlsx", ".xlsm")


@router.post("/preview")
async def preview_excel_import(
    user: AuthUserDep,
    file: UploadFile = File(...),
):
    """Devuelve la previsualización del workbook subido.

    No persiste nada — el frontend recibe el preview, deja que el
    usuario ajuste columnas/tipos y luego dispara el PUT habitual
    contra `/api/models/{id}` con las tablas resultantes.
    """
    _ensure_valid_filename(file.filename)
    payload = await _read_upload(file)

    try:
        preview = parse_workbook(payload)
    except Exception as exc:
        # openpyxl puede levantar cosas raras con archivos corruptos.
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
    """Lee el upload entero a memoria con un cap en bytes.

    `UploadFile.read()` carga todo el archivo de una; lo aceptamos
    porque ya validamos `_MAX_UPLOAD_BYTES` y queremos cortar temprano
    si el cliente está intentando subir algo más grande.
    """
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
