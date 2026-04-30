"""Endpoint de modelamiento — versión simplificada.

Pipeline:

    POST /api/conversations/{id}/model
        ↓ parsea Excel (determinista, Python)
        ↓ por cada tabla → workflow LLM (1 llamada cada uno, paralelos
                con rate limiter + semáforo)
        ↓ ensambla TableAPI (audit columns + DDL en Python)
        ↓ build ModelingResponseAPI
        ↓ persistir en state.last_model

Sin chunking, sin retry, sin completion-check, sin matching tolerante,
sin QA, sin relationships. Si una tabla falla, el response sigue con
las que sí salieron y la fallida queda registrada en logs/summary.
"""

from __future__ import annotations

import asyncio
import json
import os
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
from src.api.rate_limit import call_with_retry, llm_rate_limiter
from src.api.response_builder import (
    build_model_payload,
    build_modeling_response,
    build_modeling_response_from_payload,
)
from src.config import settings
from src.logger import get_logger
from src.schemas import ModelingResponseAPI
from src.tools.excel_tools import parse_excel_file
from src.tools.knowledge_base_tools import _resolve_guidelines, session_scope
from src.workflow.graph import run_table_pipeline

log = get_logger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["modeling"])


# Tablas procesadas en paralelo. Cada una hace 1 llamada al LLM.
# Con un output ~500 tokens/tabla, no hay riesgo de truncamiento.
# El rate limiter global (`llm_rate_limiter`) regula RPM efectivo.
try:
    _MAX_PARALLEL_TABLES = max(1, int(os.getenv("PER_TABLE_PARALLELISM") or 4))
except ValueError:
    _MAX_PARALLEL_TABLES = 4


# ─── POST /api/conversations/{id}/model ──────────────────────────────────


@router.post(
    "/{conversation_id}/model",
    response_model=ModelingResponseAPI,
    summary=(
        "Procesa el Excel adjunto y devuelve el modelo de datos con "
        "nombres físicos según los lineamientos corporativos."
    ),
)
async def generate_model(
    state: ConversationDep,
    store: StoreDep,
    client: ChatClientDep,
    semaphore: PipelineSemaphoreDep,
    excel_file: Annotated[
        UploadFile | None,
        File(description="Archivo Excel con definiciones de tablas y columnas."),
    ] = None,
    context_text: Annotated[
        str | None,
        Form(description="Texto libre del usuario (opcional, no usado por el agente)."),
    ] = None,
    engine: Annotated[
        str | None,
        Form(description="Motor de BD destino (default: el del estado)."),
    ] = None,
) -> ModelingResponseAPI:
    cid = state.conversation_id

    # ── 1. Validar input ────────────────────────────────────────────────
    if excel_file is None or not excel_file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe enviarse `excel_file` con las definiciones de las tablas.",
        )

    # ── 2. Resolver engine ──────────────────────────────────────────────
    target_engine = engine or state.engine
    if target_engine not in settings.SUPPORTED_ENGINES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Engine '{target_engine}' no soportado. "
                f"Disponibles: {settings.SUPPORTED_ENGINES}."
            ),
        )

    # ── 3. Parsear Excel ────────────────────────────────────────────────
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

    input_tables = list(excel_result.get("tables") or [])
    if not input_tables:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El Excel no contiene tablas con columnas válidas.",
        )

    log.info(
        "excel parsed",
        extra={
            "conversation_id": cid,
            "tables_parsed": len(input_tables),
            "total_columns": sum(len(t.get("columns", [])) for t in input_tables),
        },
    )

    # ── 4. Lanzar workflow por tabla en paralelo ───────────────────────
    next_turn = max(state.turn_number, 0) + 1
    started = time.monotonic()
    local_sem = asyncio.Semaphore(_MAX_PARALLEL_TABLES)

    async def _process_one(idx: int, table: dict) -> tuple[str, dict | None]:
        """Procesa una tabla. Devuelve (table_name, parsed_dict | None)."""
        tname = table.get("table_name") or f"tabla_{idx}"
        n_cols = len(table.get("columns") or [])
        async with local_sem:
            t0 = time.monotonic()

            async def _run() -> dict:
                await llm_rate_limiter.acquire(label=f"table:{tname}")
                async with semaphore:
                    return await run_table_pipeline(
                        client, table, target_engine, session_id=cid
                    )

            try:
                result = await call_with_retry(_run, label=f"table:{cid}:{tname}")
            except Exception as e:
                log.warning(
                    "table workflow raised",
                    extra={"conversation_id": cid, "table": tname,
                           "error": str(e)[:200]},
                )
                return tname, None

            if "error" in result:
                log.warning(
                    "table workflow returned error",
                    extra={"conversation_id": cid, "table": tname,
                           "error": result["error"]},
                )
                return tname, None

            try:
                parsed = json.loads(result.get("table_json") or "{}")
            except json.JSONDecodeError:
                log.warning(
                    "table json malformed",
                    extra={"conversation_id": cid, "table": tname},
                )
                return tname, None

            elapsed = int((time.monotonic() - t0) * 1000)
            log.info(
                "table modelled",
                extra={
                    "conversation_id": cid,
                    "table_input": tname,
                    "table_output": parsed.get("table_name"),
                    "cols_in": n_cols,
                    "cols_out": len(parsed.get("columns") or []),
                    "ms": elapsed,
                },
            )
            return tname, parsed

    tasks = [_process_one(i, t) for i, t in enumerate(input_tables)]
    pairs: list[tuple[str, dict | None]] = await asyncio.gather(*tasks)

    raw_tables: list[dict] = []
    failed: list[str] = []
    for tname, parsed in pairs:
        if parsed is None:
            failed.append(tname)
            continue
        raw_tables.append(parsed)

    if not raw_tables:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Ninguna tabla pudo ser modelada. "
                "Revisa los logs del servidor para más detalle."
            ),
        )

    # ── 5. Resolver guidelines (para audit columns) ────────────────────
    with session_scope(cid):
        guidelines = _resolve_guidelines()
    if "error" in (guidelines or {}):
        guidelines = {}

    # ── 6. Ensamblar payload determinista y persistir ──────────────────
    payload = build_model_payload(raw_tables, target_engine, guidelines=guidelines)

    elapsed_ms = int((time.monotonic() - started) * 1000)

    async with state.lock:
        state.engine = target_engine
        state.history.append(
            {"role": "user",
             "content": _summarize_user_turn(context_text or "", input_tables)}
        )
        state.history.append(
            {"role": "assistant",
             "content": _summarize_assistant_turn(payload, failed)}
        )
        state.last_model = payload
        state.turn_number = next_turn
        state.touch()

    log.info(
        "pipeline finished",
        extra={
            "conversation_id": cid,
            "turn": state.turn_number,
            "engine": target_engine,
            "ms": elapsed_ms,
            "tables_in": len(input_tables),
            "tables_out": len(payload.get("tables") or []),
            "failed": len(failed),
        },
    )

    # ── 7. Construir response ──────────────────────────────────────────
    return build_modeling_response(
        state, raw_tables, target_engine, guidelines=guidelines,
    )


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
    """Texto compacto del turno del usuario para el historial."""
    parts: list[str] = []
    if user_text.strip():
        parts.append(user_text.strip())
    if tables:
        total_cols = sum(len(t.get("columns", []) or []) for t in tables)
        parts.append(
            f"[Excel adjunto: {len(tables)} tabla(s), {total_cols} columna(s)]"
        )
    return "\n".join(parts) if parts else "(turno vacío)"


def _summarize_assistant_turn(payload: dict, failed: list[str]) -> str:
    """Texto compacto del turno del agente para el historial."""
    tables = payload.get("tables") or []
    table_names = ", ".join(
        t.get("table_name", "") for t in tables if t.get("table_name")
    )
    parts: list[str] = []
    if table_names:
        parts.append(f"Tablas modeladas: {table_names}")
    if failed:
        parts.append(f"Falladas: {', '.join(failed)}")
    return " | ".join(parts) if parts else "(modelo generado)"
