"""Endpoints de conversaciones y guidelines.

- POST /api/conversations
- POST /api/conversations/{conversation_id}/guidelines
- DELETE /api/conversations/{conversation_id}
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from src.api.dependencies import (
    ConversationDep,
    StoreDep,
    validate_uuid_v4,
)
from src.config import settings
from src.conversation import GuidelinesMeta
from src.logger import get_logger
from src.schemas import (
    CreateConversationRequest,
    CreateConversationResponse,
    DeleteConversationResponse,
    GuidelinesUploadResponse,
)
from src.tools.knowledge_base_tools import (
    process_guidelines_file,
    set_session_guidelines,
)

log = get_logger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


# ─── Formatos soportados de guidelines ──────────────────────────────────

_SUPPORTED_EXTS: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".md": "md",
    ".json": "json",
    ".txt": "txt",
    ".xlsx": "xlsx",
}

_PREVIEW_LEN = 500


def _detect_format(filename: str) -> str:
    """Devuelve el formato detectado o lanza 400 si no está soportado."""
    suffix = Path(filename).suffix.lower()
    fmt = _SUPPORTED_EXTS.get(suffix)
    if fmt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Formato no soportado: '{suffix}'. "
                f"Aceptados: {', '.join(sorted(_SUPPORTED_EXTS))}."
            ),
        )
    return fmt


def _build_preview(processed: dict, max_len: int = _PREVIEW_LEN) -> str:
    """Genera un preview legible del contenido procesado."""
    if "error" in processed:
        return f"[error] {processed['error']}"
    if "content" in processed:  # PDF/DOCX/MD/TXT
        return str(processed["content"])[:max_len]
    # JSON/XLSX: serializamos y recortamos.
    import json as _json

    try:
        text = _json.dumps(processed, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        text = str(processed)
    return text[:max_len]


# ─── POST /api/conversations ────────────────────────────────────────────


@router.post(
    "",
    response_model=CreateConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crea una nueva conversación o devuelve la existente.",
)
async def create_conversation(
    payload: CreateConversationRequest,
    store: StoreDep,
) -> CreateConversationResponse:
    engine = payload.engine or settings.DEFAULT_DB_ENGINE
    if engine not in settings.SUPPORTED_ENGINES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Engine '{engine}' no soportado. "
                f"Disponibles: {settings.SUPPORTED_ENGINES}."
            ),
        )

    pre_existing = await store.exists(payload.conversation_id)
    state = await store.get_or_create(payload.conversation_id, engine)
    # Si la conversación ya existía con otro engine y el caller solicitó uno
    # nuevo, lo actualizamos para que el siguiente turno lo use.
    if pre_existing and state.engine != engine:
        await store.set_engine(payload.conversation_id, engine)
        state.engine = engine

    # NOTA: NO usar la clave `created` en `extra=`. Es un atributo reservado
    # del `LogRecord` (timestamp del record) y `logging.makeRecord` lanza
    # `KeyError: "Attempt to overwrite 'created' in LogRecord"`. Renombrado
    # a `was_created` para evitar la colisión.
    log.info(
        "conversation upserted",
        extra={
            "conversation_id": payload.conversation_id,
            "engine": engine,
            "was_created": not pre_existing,
        },
    )
    return CreateConversationResponse(
        conversation_id=payload.conversation_id,
        engine=engine,
        created=not pre_existing,
    )


# ─── POST /api/conversations/{id}/guidelines ────────────────────────────


@router.post(
    "/{conversation_id}/guidelines",
    response_model=GuidelinesUploadResponse,
    summary="Carga las guidelines para una conversación.",
)
async def upload_guidelines(
    state: ConversationDep,
    file: Annotated[UploadFile, File(description="Archivo de guidelines.")],
) -> GuidelinesUploadResponse:
    if file.filename is None or not file.filename.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo subido no tiene nombre.",
        )

    fmt = _detect_format(file.filename)

    # Persistimos en un tempfile con el suffix correcto para que docling /
    # openpyxl puedan inferir el tipo. Lo borramos al final.
    suffix = Path(file.filename).suffix.lower()
    raw = await file.read()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo subido está vacío.",
        )

    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=suffix, prefix="guidelines_"
    )
    try:
        tmp.write(raw)
        tmp.flush()
        tmp.close()
        processed = process_guidelines_file(tmp.name)
    finally:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except OSError:
            log.warning(
                "could not remove tmp guidelines file",
                extra={"path": tmp.name},
            )

    if "error" in processed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=processed["error"],
        )

    # Guardar contenido procesado en el cache de la sesión.
    set_session_guidelines(state.conversation_id, processed)

    preview = _build_preview(processed)
    meta = GuidelinesMeta(
        file_name=file.filename,
        file_format=fmt,
        file_size_bytes=len(raw),
        preview=preview,
    )
    # Necesitamos el store para registrar la metadata: lo obtenemos via state.
    # Pero ConversationDep ya nos dio la state; guardamos el meta directo.
    async with state.lock:
        state.guidelines_meta = meta
        state.touch()

    log.info(
        "guidelines uploaded",
        extra={
            "conversation_id": state.conversation_id,
            "file_format": fmt,
            "file_size_bytes": len(raw),
            "file_name": file.filename,
        },
    )

    return GuidelinesUploadResponse(
        status="ok",
        conversation_id=state.conversation_id,
        file_name=file.filename,
        file_format=fmt,
        file_size_bytes=len(raw),
        preview=preview,
    )


# ─── DELETE /api/conversations/{id} ─────────────────────────────────────


@router.delete(
    "/{conversation_id}",
    response_model=DeleteConversationResponse,
    summary="Elimina una conversación y libera su memoria.",
)
async def delete_conversation(
    conversation_id: str,
    store: StoreDep,
) -> DeleteConversationResponse:
    validate_uuid_v4(conversation_id)
    deleted = await store.clear(conversation_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversación '{conversation_id}' no encontrada.",
        )
    log.info(
        "conversation deleted",
        extra={"conversation_id": conversation_id},
    )
    return DeleteConversationResponse(
        conversation_id=conversation_id,
        deleted=True,
    )
