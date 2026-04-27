"""Endpoint principal de modelamiento.

- POST /api/conversations/{conversation_id}/model: ejecuta el pipeline.
- GET /api/conversations/{conversation_id}/model: retorna el último modelo
  generado sin reejecutar el agente.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from src.api.dependencies import (
    ChatClientDep,
    ConversationDep,
    PipelineSemaphoreDep,
    StoreDep,
)
from src.api.response_builder import (
    build_model_payload,
    build_modeling_response,
    build_modeling_response_from_payload,
)
from src.config import settings
from src.logger import get_logger
from src.schemas import ModelingResponseAPI
from src.tools.excel_tools import parse_excel_file
from src.workflow.graph import run_modeling_pipeline

log = get_logger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["modeling"])


# ─── POST /api/conversations/{id}/model ─────────────────────────────────


@router.post(
    "/{conversation_id}/model",
    response_model=ModelingResponseAPI,
    summary=(
        "Ejecuta el pipeline de modelamiento usando el historial y el último "
        "modelo de la conversación."
    ),
)
async def generate_model(
    state: ConversationDep,
    store: StoreDep,
    client: ChatClientDep,
    semaphore: PipelineSemaphoreDep,
    excel_file: Annotated[
        UploadFile | None,
        File(description="Archivo Excel opcional con tablas/columnas."),
    ] = None,
    context_text: Annotated[
        str | None,
        Form(description="Texto libre del usuario."),
    ] = None,
    engine: Annotated[
        str | None,
        Form(description="Motor de BD para este turno (opcional)."),
    ] = None,
) -> ModelingResponseAPI:
    cid = state.conversation_id

    # ── 1. Validar input mínimo ────────────────────────────────────────
    has_excel = excel_file is not None and excel_file.filename
    has_text = context_text is not None and context_text.strip()
    if not has_excel and not has_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe enviarse al menos `excel_file` o `context_text`.",
        )

    # ── 2. Resolver engine ─────────────────────────────────────────────
    target_engine = engine or state.engine
    if target_engine not in settings.SUPPORTED_ENGINES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Engine '{target_engine}' no soportado. "
                f"Disponibles: {settings.SUPPORTED_ENGINES}."
            ),
        )

    # ── 3. Parsear Excel si vino ───────────────────────────────────────
    tables: list[dict[str, Any]] = []
    if has_excel:
        suffix = Path(excel_file.filename or "").suffix.lower()
        if suffix != ".xlsx":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"`excel_file` debe ser .xlsx (recibido: {suffix}).",
            )
        raw = await excel_file.read()
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El archivo Excel está vacío.",
            )
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".xlsx", prefix="modeling_"
        )
        try:
            tmp.write(raw)
            tmp.flush()
            tmp.close()
            excel_result_str = parse_excel_file.func(tmp.name)
        finally:
            try:
                Path(tmp.name).unlink(missing_ok=True)
            except OSError:
                log.warning(
                    "could not remove tmp excel file",
                    extra={"path": tmp.name},
                )
        try:
            excel_result = json.loads(excel_result_str)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No se pudo parsear el resultado del archivo Excel.",
            )
        if "error" in excel_result:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Error al parsear Excel: {excel_result['error']}",
            )
        tables = list(excel_result.get("tables") or [])

    # ── 4. Construir input del pipeline ────────────────────────────────
    user_text = context_text or ""

    # Snapshot del historial y último modelo bajo el lock de la conversación.
    async with state.lock:
        history = list(state.history)
        last_model = state.last_model

    input_data: dict[str, Any] = {
        "user_text": user_text,
        "tables": tables,
        "target_engine": target_engine,
        "relationships": [],
        "history": history,
        "last_model": last_model or {},
        "session_id": cid,
    }

    # ── 5. Ejecutar pipeline bajo semaphore ────────────────────────────
    next_turn = state.turn_number + 1
    log.info(
        "pipeline starting",
        extra={
            "conversation_id": cid,
            "turn": next_turn,
            "engine": target_engine,
            "has_excel": has_excel,
            "has_text": has_text,
        },
    )
    started = time.monotonic()
    try:
        async with semaphore:
            workflow_result = await run_modeling_pipeline(client, input_data)
    except Exception as e:
        log.exception(
            "pipeline crashed",
            extra={"conversation_id": cid, "turn": next_turn},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error interno al ejecutar el pipeline: {e}",
        )
    elapsed_ms = int((time.monotonic() - started) * 1000)

    if "error" in workflow_result:
        log.error(
            "pipeline returned error",
            extra={
                "conversation_id": cid,
                "turn": next_turn,
                "error": workflow_result["error"],
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=workflow_result["error"],
        )

    # ── 6. Construir payload persistible y response ────────────────────
    payload = build_model_payload(workflow_result)

    # Engine efectivo según lo que vuelve del workflow (puede traer el mismo).
    effective_engine = str(workflow_result.get("engine") or target_engine)

    # ── 7. Persistir en el store (turn_number se incrementa aquí) ──────
    async with state.lock:
        state.engine = effective_engine
        state.history.append(
            {
                "role": "user",
                "content": _summarize_user_turn(user_text, tables),
            }
        )
        state.history.append(
            {
                "role": "assistant",
                "content": _summarize_assistant_turn(payload),
            }
        )
        state.last_model = payload
        state.turn_number += 1
        state.touch()

    log.info(
        "pipeline finished",
        extra={
            "conversation_id": cid,
            "turn": state.turn_number,
            "engine": effective_engine,
            "ms": elapsed_ms,
            "tables": len(payload.get("tables") or []),
        },
    )

    response = build_modeling_response(state, workflow_result)
    return response


# ─── GET /api/conversations/{id}/model ──────────────────────────────────


@router.get(
    "/{conversation_id}/model",
    response_model=ModelingResponseAPI,
    summary="Retorna el último modelo generado en la conversación.",
)
async def get_last_model(state: ConversationDep) -> ModelingResponseAPI:
    if state.last_model is None or not state.last_model.get("tables"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Esta conversación todavía no tiene un modelo generado. "
                "Invoca primero POST /api/conversations/{id}/model."
            ),
        )
    return build_modeling_response_from_payload(state, state.last_model)


# ─── Helpers de resumen de turno ────────────────────────────────────────


def _summarize_user_turn(user_text: str, tables: list[dict]) -> str:
    """Texto compacto del turno del usuario para guardar en el historial."""
    parts: list[str] = []
    if user_text.strip():
        parts.append(user_text.strip())
    if tables:
        total_cols = sum(len(t.get("columns", []) or []) for t in tables)
        parts.append(
            f"[Excel adjunto: {len(tables)} tabla(s), {total_cols} columna(s)]"
        )
    return "\n".join(parts) if parts else "(turno vacío)"


def _summarize_assistant_turn(payload: dict) -> str:
    """Texto compacto del turno del agente para guardar en el historial.

    No copiamos todo el modelo (eso ya queda en `last_model`); solo un
    resumen accionable que ayude al agente a recordar qué generó.
    """
    tables = payload.get("tables") or []
    table_names = ", ".join(t.get("table_name", "") for t in tables if t.get("table_name"))
    summary = payload.get("summary") or ""
    parts: list[str] = []
    if table_names:
        parts.append(f"Tablas modeladas: {table_names}")
    if summary:
        parts.append(summary)
    return " | ".join(parts) if parts else "(modelo generado)"
