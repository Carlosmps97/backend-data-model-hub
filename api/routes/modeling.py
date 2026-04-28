"""Endpoint principal de modelamiento.

- POST /api/conversations/{conversation_id}/model: ejecuta el pipeline.
- GET /api/conversations/{conversation_id}/model: retorna el último modelo
  generado sin reejecutar el agente.
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
from src.workflow.graph import run_modeling_pipeline

log = get_logger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["modeling"])

# Tablas procesadas en paralelo por request. Cada tabla puede gatillar
# 1+ llamadas al LLM (chunking interno por columnas). Bajamos a 2 para
# reducir RPM y dejar espacio al rate limiter global.
try:
    _MAX_PARALLEL_TABLES = max(1, int(os.getenv("PER_TABLE_PARALLELISM") or 2))
except ValueError:
    _MAX_PARALLEL_TABLES = 2

# Tamaño máximo de un sub-batch de columnas dentro de una tabla. Cuando
# una tabla tiene MÁS columnas que esto, se parte en chunks y cada uno se
# procesa como una llamada al LLM independiente. Esto garantiza que el
# output nunca se trunque, INDEPENDIENTEMENTE del cap de tokens del
# modelo. 25 es un valor conservador: 25 columnas + DDL + descripciones
# en español ≈ 4–6K tokens por respuesta, muy por debajo de cualquier cap.
try:
    _COLS_PER_BATCH = max(5, int(os.getenv("COLS_PER_BATCH") or 25))
except ValueError:
    _COLS_PER_BATCH = 25

# Umbral mínimo de columnas para considerar la tabla "completa". Si el
# agente devuelve menos del 80 % de las columnas del input, se hace un
# reintento dirigido pidiendo SOLO las columnas faltantes. Es la red de
# seguridad final por encima del chunking.
_MIN_COLUMN_COVERAGE = 0.8


def _build_merged_qa() -> dict:
    return {
        "quality_score": 0,
        "standardized_columns": [],
        "guideline_violations": [],
        "new_catalog_entries": [],
        "summary": "",
    }


def _merge_qa_into(merged: dict, qa: dict, successful_count: int) -> None:
    """Acumula un qa_report en merged con media ponderada del score."""
    merged["standardized_columns"].extend(qa.get("standardized_columns") or [])
    merged["guideline_violations"].extend(qa.get("guideline_violations") or [])
    merged["new_catalog_entries"].extend(qa.get("new_catalog_entries") or [])
    score = qa.get("quality_score") or 0
    if successful_count == 1:
        merged["quality_score"] = score
    else:
        merged["quality_score"] = int(
            (merged["quality_score"] * (successful_count - 1) + score) / successful_count
        )


def _input_column_names(table: dict) -> list[str]:
    """Devuelve los nombres de columnas del Excel (lowercased)."""
    cols = table.get("columns") or []
    out: list[str] = []
    for c in cols:
        name = (c.get("column_name") or "").strip()
        if name:
            out.append(name.lower())
    return out


def _output_column_names(out_table: dict) -> set[str]:
    """Devuelve el set de nombres de columnas en la salida (lowercased)."""
    cols = out_table.get("columns") or []
    return {(c.get("column_name") or "").strip().lower() for c in cols if c.get("column_name")}


def _missing_input_columns(table: dict, out_payload: dict) -> list[dict]:
    """Detecta columnas del Excel que el agente NO devolvió.

    El matching es por nombre exacto (case-insensitive). Esto da un falso
    positivo cuando el agente renombra una columna (ej. `agent_id` →
    `id_agent`), pero como solo lo usamos para el reintento dirigido y el
    prompt de retry pide explícitamente reusar nombres del input, en la
    práctica recupera tablas truncadas sin generar duplicados.
    """
    out_tables = out_payload.get("tables") or []
    if not out_tables:
        return list(table.get("columns") or [])
    out_cols = set()
    for ot in out_tables:
        out_cols |= _output_column_names(ot)

    missing: list[dict] = []
    for c in table.get("columns") or []:
        name = (c.get("column_name") or "").strip().lower()
        if name and name not in out_cols:
            missing.append(c)
    return missing


def _merge_table_columns(into: dict, extra: dict) -> None:
    """Mergea las columnas de `extra` dentro de `into`, evitando duplicados.

    Útil cuando un reintento dirigido devuelve solo las columnas que
    faltaban — las apendamos al final del array de columnas existente.
    """
    into_cols = into.setdefault("columns", [])
    seen = {c.get("column_name", "").strip().lower() for c in into_cols if c.get("column_name")}
    for c in extra.get("columns") or []:
        name = (c.get("column_name") or "").strip().lower()
        if name and name not in seen:
            into_cols.append(c)
            seen.add(name)


async def _run_one_chunk(
    client: Any,
    base_input: dict[str, Any],
    table: dict[str, Any],
    columns_chunk: list[dict],
    cid: str,
    idx: int,
    chunk_label: str,
    *,
    extra_user_text: str = "",
) -> dict[str, Any]:
    """Ejecuta el pipeline lean para UN sub-batch de columnas de una tabla.

    El "chunk" corresponde a un subconjunto de las columnas de la tabla.
    Esta función es invocada una o más veces por `_run_one_table` según
    el tamaño total de la tabla.

    - Aplica el rate limiter global antes de la llamada al LLM.
    - Reintenta automáticamente si Azure responde 429 (backoff
      exponencial con jitter).
    - El prompt incluye el contexto completo de la tabla, pero le pide
      al modelo trabajar SOLO con las columnas del chunk.
    """
    tname = table.get("table_name", f"tabla_{idx}")
    tdesc = table.get("table_description", "")
    total_cols_in_table = len(table.get("columns") or [])
    chunk_size = len(columns_chunk)

    # Enriquecer user_text con contexto de la tabla y del chunking.
    base_text = (base_input.get("user_text") or "").strip()
    parts: list[str] = []
    if base_text:
        parts.append(base_text)
    parts.append(f'Tabla a modelar: "{tname}"')
    if tdesc:
        parts.append(f"Descripción funcional: {tdesc}")
    if total_cols_in_table != chunk_size:
        # Estamos en chunking: explicar el contexto al modelo para que
        # entienda que las columnas que ve son un subset y no inventar
        # columnas adicionales fuera del chunk.
        parts.append(
            f"NOTA TÉCNICA — chunking activado: la tabla original tiene "
            f"{total_cols_in_table} columnas en total, pero acá solo se te "
            f"están pasando {chunk_size}. Generá EXCLUSIVAMENTE estas "
            f"{chunk_size} columnas en el JSON de salida, con los mismos "
            "nombres físicos que recibís en el array `columns` de la tabla "
            "(podés normalizarlos según los lineamientos pero NO los inventes "
            "ni los omitas). NO agregues columnas de auditoría adicionales "
            "más allá de las que ya estén en el input. El JSON debe tener "
            "una sola tabla con esas columnas y su DDL correspondiente."
        )
    if extra_user_text:
        parts.append(extra_user_text.strip())
    enriched = "\n\n".join(parts)

    # Pasamos la tabla con el subset de columnas del chunk.
    table_for_pipeline = dict(table)
    table_for_pipeline["columns"] = columns_chunk

    single_input = {
        **base_input,
        "tables": [table_for_pipeline],
        "user_text": enriched,
        # last_model vacío para mantener cada tabla / chunk independiente.
        "last_model": {},
    }

    async def _call() -> dict[str, Any]:
        await llm_rate_limiter.acquire(label=f"chunk:{tname}:{chunk_label}")
        # Modo lean: ExecutorAgent sin QAValidator. Reduce ~50 % de
        # llamadas al LLM por chunk y elimina el bucle de N
        # search_column_catalog (causa principal del rate limit).
        return await run_modeling_pipeline(client, single_input, lean=True)

    return await call_with_retry(_call, label=f"chunk:{tname}:{chunk_label}")


def _chunk_columns(columns: list[dict], size: int) -> list[list[dict]]:
    """Divide la lista de columnas en chunks de tamaño `size`."""
    if size <= 0 or not columns:
        return [columns] if columns else []
    return [columns[i : i + size] for i in range(0, len(columns), size)]


async def _run_one_table(
    client: Any,
    base_input: dict[str, Any],
    table: dict[str, Any],
    cid: str,
    idx: int,
) -> dict[str, Any]:
    """Procesa una tabla COMPLETA con chunking adaptativo por columnas.

    Estrategia:
    - Si la tabla tiene ≤ `_COLS_PER_BATCH` columnas → 1 sola llamada.
    - Si tiene MÁS → se parte en chunks y cada uno corre en paralelo
      bajo el mismo rate limiter (así no se desincroniza con otras
      tablas que estén procesándose).
    - Los resultados de cada chunk se mergean en una sola tabla con el
      orden original de columnas.

    Devuelve un dict con el mismo formato que `run_modeling_pipeline`,
    o `{"error": ...}` si falló alguna parte crítica.
    """
    tname = table.get("table_name", f"tabla_{idx}")
    columns = list(table.get("columns") or [])
    total = len(columns)
    chunks = _chunk_columns(columns, _COLS_PER_BATCH)
    n_chunks = len(chunks) if chunks else 1

    if n_chunks <= 1:
        # Camino corto: una sola llamada.
        return await _run_one_chunk(
            client,
            base_input,
            table,
            columns or [],
            cid,
            idx,
            chunk_label="0/1",
        )

    log.info(
        "table chunking",
        extra={
            "conversation_id": cid,
            "table": tname,
            "idx": idx,
            "total_cols": total,
            "chunks": n_chunks,
            "cols_per_batch": _COLS_PER_BATCH,
        },
    )

    # Lanzar todos los chunks en paralelo. El rate limiter global los
    # serializa correctamente sin necesitar otro semáforo aquí: si dos
    # chunks se largan en simultáneo y ambos exceden el RPM, uno de los
    # dos se queda esperando a `acquire()`.
    chunk_tasks = [
        _run_one_chunk(
            client,
            base_input,
            table,
            ch,
            cid,
            idx,
            chunk_label=f"{i + 1}/{n_chunks}",
        )
        for i, ch in enumerate(chunks)
    ]
    chunk_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)

    # Mergear todos los chunks en una sola "tabla" + un solo
    # `generated_model` con la forma que espera `build_model_payload`.
    merged_columns: list[dict] = []
    seen: set[str] = set()
    merged_table: dict[str, Any] | None = None
    chunk_failures = 0

    for cr in chunk_results:
        if isinstance(cr, Exception):
            chunk_failures += 1
            log.warning(
                "chunk failed after retries",
                extra={"conversation_id": cid, "table": tname,
                       "error": str(cr)[:200]},
            )
            continue
        if "error" in cr:
            chunk_failures += 1
            log.warning(
                "chunk returned error",
                extra={"conversation_id": cid, "table": tname,
                       "error": cr.get("error")},
            )
            continue
        # Extraer la tabla de este chunk.
        partial = build_model_payload(cr)
        ptables = partial.get("tables") or []
        if not ptables:
            continue
        ptable = ptables[0]
        if merged_table is None:
            # Primera tabla con valor: usamos sus metadatos (name, ddl,
            # comment, etc.) y vamos extendiendo `columns`.
            merged_table = dict(ptable)
            merged_table["columns"] = []
        for c in ptable.get("columns") or []:
            cname = (c.get("column_name") or "").strip().lower()
            if not cname:
                continue
            if cname in seen:
                continue
            seen.add(cname)
            merged_columns.append(c)

    if merged_table is None:
        return {
            "error": (
                f"Todos los chunks de la tabla '{tname}' fallaron "
                f"({chunk_failures}/{n_chunks})."
            )
        }

    merged_table["columns"] = merged_columns

    # Reconstruimos el shape que devuelve `run_modeling_pipeline` para
    # que `process_one` lo trate igual que el caso de 1-call.
    payload = {
        "engine": base_input.get("target_engine") or "databricks_sql",
        "generated_model": {"tables": [merged_table]},
        "qa_validation": "{}",
    }

    if chunk_failures > 0:
        log.info(
            "table chunked with partial failures",
            extra={"conversation_id": cid, "table": tname,
                   "chunks_ok": n_chunks - chunk_failures,
                   "chunks_failed": chunk_failures,
                   "cols_merged": len(merged_columns), "expected": total},
        )

    return payload


async def _run_per_table_pipeline(
    client: Any,
    base_input: dict[str, Any],
    global_semaphore: Any,
    cid: str,
    next_turn: int,
) -> dict[str, Any]:
    """Ejecuta un pipeline lean (Executor sin QA) por cada tabla del Excel.

    Estrategia (3 niveles):
    1. **Per-tabla**: las tablas del Excel se procesan en paralelo,
       hasta `_MAX_PARALLEL_TABLES` simultáneas dentro del request.
    2. **Per-chunk de columnas**: cada tabla con MÁS de
       `_COLS_PER_BATCH` columnas se parte en chunks. Cada chunk =
       1 llamada al LLM. Garantiza output completo independientemente
       del cap de tokens del modelo.
    3. **Completion-check con reintento dirigido**: si después del
       chunking todavía faltan columnas (caso raro: el modelo no
       respetó la fidelidad), se reintenta SOLO con las columnas que
       faltan.

    Cada llamada al LLM pasa por `call_with_retry` (backoff exponencial
    para 429) y `llm_rate_limiter.acquire()` (token bucket por minuto).

    Retorna dict compatible con `build_model_payload`:
      {"tables": [...], "relationships": [...], "qa_report": {...}, "summary": str}
    """
    tables = base_input.get("tables", [])
    total = len(tables)
    local_sem = asyncio.Semaphore(_MAX_PARALLEL_TABLES)

    log.info(
        "per-table pipeline starting",
        extra={"conversation_id": cid, "turn": next_turn, "total_tables": total,
               "parallel": _MAX_PARALLEL_TABLES, "mode": "lean"},
    )

    async def process_one(table: dict, idx: int) -> dict | None:
        """Procesa una sola tabla con retries y completion-check."""
        tname = table.get("table_name", f"tabla_{idx}")
        input_cols = _input_column_names(table)
        expected = len(input_cols)

        t0 = time.monotonic()
        async with local_sem:                # paralelismo dentro del request
            async with global_semaphore:     # límite de pipelines concurrentes
                # ── Pase 1: pedir todas las columnas ─────────────────────
                try:
                    result = await _run_one_table(
                        client, base_input, table, cid, idx,
                    )
                except Exception as e:  # 429 agotado u otro error de transporte
                    log.warning(
                        "table pipeline failed after retries",
                        extra={"conversation_id": cid, "table": tname,
                               "idx": idx, "error": str(e)[:200]},
                    )
                    return None

                if "error" in result:
                    log.warning(
                        "table pipeline returned error",
                        extra={"conversation_id": cid, "table": tname,
                               "idx": idx, "error": result["error"]},
                    )
                    return None

                payload = build_model_payload(result)

                # ── Pase 2 (opcional): completion-check ─────────────────
                # Si el agente devolvió MENOS del 80 % de las columnas del
                # input, asumimos truncamiento y reintentamos con SOLO las
                # columnas que faltan. Esto recupera tablas grandes que
                # cayeron contra el max_output_tokens del modelo.
                missing = _missing_input_columns(table, payload)
                got = expected - len(missing)
                coverage = (got / expected) if expected else 1.0

                if expected > 0 and missing and coverage < _MIN_COLUMN_COVERAGE:
                    log.info(
                        "table coverage low — running targeted retry",
                        extra={"conversation_id": cid, "table": tname,
                               "idx": idx, "got": got, "expected": expected,
                               "coverage": round(coverage, 2),
                               "missing": len(missing)},
                    )
                    extra_text = (
                        f"ATENCIÓN: en un intento previo el modelo devolvió "
                        f"solo {got}/{expected} columnas (truncamiento o "
                        f"falta de fidelidad). Generá AHORA EXCLUSIVAMENTE "
                        f"las {len(missing)} columnas faltantes que se "
                        "listan en el input, con el mismo nombre físico "
                        "(o el normalizado por los lineamientos). NO "
                        "repitas columnas ya generadas. Devolvé el JSON "
                        "con UNA sola tabla con SOLO esas columnas y su "
                        "DDL correspondiente."
                    )
                    try:
                        result2 = await _run_one_chunk(
                            client, base_input, table, missing, cid, idx,
                            chunk_label="retry",
                            extra_user_text=extra_text,
                        )
                    except Exception as e:
                        log.warning(
                            "targeted retry failed",
                            extra={"conversation_id": cid, "table": tname,
                                   "error": str(e)[:200]},
                        )
                    else:
                        if "error" not in result2:
                            payload2 = build_model_payload(result2)
                            # Mergeamos las columnas del retry dentro de
                            # la primera tabla del payload original.
                            extra_tables = payload2.get("tables") or []
                            base_tables = payload.get("tables") or []
                            if base_tables and extra_tables:
                                _merge_table_columns(base_tables[0], extra_tables[0])

        elapsed = int((time.monotonic() - t0) * 1000)
        tables_out = payload.get("tables") or []
        cols_out = sum(len(t.get("columns") or []) for t in tables_out)
        log.info(
            "table done",
            extra={"conversation_id": cid, "table": tname, "idx": idx,
                   "tables_out": len(tables_out), "cols_out": cols_out,
                   "expected_cols": expected, "ms": elapsed},
        )
        return payload

    # ── Lanzar todas las tareas en paralelo ─────────────────────────────
    tasks = [process_one(t, i) for i, t in enumerate(tables, 1)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # ── Mergear en el orden original del Excel ────────────────────────
    merged_tables: list[dict] = []
    merged_rels: list[str] = []
    merged_qa = _build_merged_qa()
    successful = 0
    failed_names: list[str] = []

    for tbl_idx, payload in enumerate(results):
        if payload is None or isinstance(payload, Exception):
            failed_names.append(tables[tbl_idx].get("table_name", f"tabla_{tbl_idx + 1}"))
            if isinstance(payload, Exception):
                log.warning(
                    "table task raised exception",
                    extra={"conversation_id": cid, "error": str(payload)[:200]},
                )
            continue
        merged_tables.extend(payload.get("tables") or [])
        merged_rels.extend(payload.get("relationships") or [])
        successful += 1
        _merge_qa_into(merged_qa, payload.get("qa_report") or {}, successful)

    table_names = ", ".join(t.get("table_name", "") for t in merged_tables if t.get("table_name"))
    if failed_names:
        summary = (
            f"Modelo generado: {len(merged_tables)} de {total} tabla(s). "
            f"Falladas ({len(failed_names)}): {', '.join(failed_names)}. "
            f"Tablas OK: {table_names}."
        )
    else:
        summary = (
            f"Modelo generado: {len(merged_tables)} tabla(s). Tablas: {table_names}."
        )
    merged_qa["summary"] = summary

    log.info(
        "per-table pipeline finished",
        extra={"conversation_id": cid, "turn": next_turn, "total": total,
               "successful": successful, "tables_merged": len(merged_tables),
               "failed": len(failed_names)},
    )

    return {"tables": merged_tables, "relationships": merged_rels,
            "qa_report": merged_qa, "summary": summary}


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
        log.info(
            "excel parsed",
            extra={
                "conversation_id": cid,
                "tables_parsed": len(tables),
                "total_columns": sum(len(t.get("columns", [])) for t in tables),
                "has_table_definitions": excel_result.get("has_table_definitions", False),
            },
        )

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

    # ── 5. Ejecutar pipeline ──────────────────────────────────────────────
    next_turn = state.turn_number + 1
    # Cuando hay tablas de un Excel usamos el modo per-tabla:
    # 1 tabla = 1 llamada al LLM → output acotado, sin truncamiento.
    use_per_table = has_excel and len(tables) > 0

    log.info(
        "pipeline starting",
        extra={
            "conversation_id": cid,
            "turn": next_turn,
            "engine": target_engine,
            "has_excel": has_excel,
            "has_text": has_text,
            "input_tables": len(tables),
            "mode": "per-table" if use_per_table else "single",
        },
    )
    started = time.monotonic()

    try:
        if use_per_table:
            # Per-tabla: cada tabla corre el pipeline LEAN (Executor sin QA)
            # con rate limit + retry + completion-check.
            payload = await _run_per_table_pipeline(
                client, input_data, semaphore, cid, next_turn
            )
            effective_engine = target_engine

        else:
            # Modo estándar: texto libre sin tablas (refinamiento
            # conversacional). Mantenemos el grafo completo con
            # QAValidatorAgent porque ahí SÍ aporta valor (estandariza
            # nombres, reporta violaciones, calcula quality_score).
            # Aplicamos rate limit + retry sobre el pipeline completo.
            async def _run_full() -> dict:
                await llm_rate_limiter.acquire(label="single-turn")
                async with semaphore:
                    return await run_modeling_pipeline(client, input_data)

            workflow_result = await call_with_retry(
                _run_full, label=f"single-turn:{cid}"
            )
            if "error" in workflow_result:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=workflow_result["error"],
                )
            payload = build_model_payload(workflow_result)
            effective_engine = str(workflow_result.get("engine") or target_engine)

    except HTTPException:
        raise
    except Exception as e:
        log.exception("pipeline crashed", extra={"conversation_id": cid, "turn": next_turn})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error interno al ejecutar el pipeline: {e}",
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)

    # ── 6. Persistir en el store ──────────────────────────────────────
    async with state.lock:
        state.engine = effective_engine
        state.history.append({"role": "user", "content": _summarize_user_turn(user_text, tables)})
        state.history.append({"role": "assistant", "content": _summarize_assistant_turn(payload)})
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
            "tables_out": len(payload.get("tables") or []),
            "mode": "per-table" if use_per_table else "single",
        },
    )

    # ── 7. Construir response ─────────────────────────────────────────
    if use_per_table:
        response = build_modeling_response_from_payload(state, payload)
    else:
        response = build_modeling_response(state, workflow_result)  # type: ignore[possibly-undefined]

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
