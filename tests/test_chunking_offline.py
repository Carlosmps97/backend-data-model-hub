"""Smoke test offline para la lógica de chunking + merge + completion check.

NO llama al LLM — mockea `run_modeling_pipeline` con una función local.
Sirve para validar la mecánica del per-table pipeline sin gastar tokens.

Run:
    /path/to/.venv/bin/python tests/test_chunking_offline.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Hacer importable el paquete del backend.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Forzamos los defaults antes de importar el módulo.
os.environ.setdefault("COLS_PER_BATCH", "25")
os.environ.setdefault("PER_TABLE_PARALLELISM", "2")
os.environ.setdefault("LLM_RPM_CAP", "30")
os.environ.setdefault("LLM_MAX_RETRIES", "0")  # offline, no queremos reintentar

# IMPORTANTE: cargar primero rate_limit antes de modeling para mockear.
from src.api import rate_limit as RL  # noqa: E402
from src.workflow import graph as G  # noqa: E402

# Mock: rate limiter que no espera.
class _NoopLimiter:
    async def acquire(self, label: str = "") -> None:
        return None


# Mock: pipeline que devuelve siempre el subset que recibe.
_call_count = [0]
_captured: list[dict] = []


async def _fake_pipeline(client, single_input: dict, *, lean: bool = False) -> dict:
    _call_count[0] += 1
    _captured.append(single_input)
    table = single_input["tables"][0]
    cols = table["columns"]
    return {
        "engine": single_input.get("target_engine") or "databricks_sql",
        "generated_model": {
            "tables": [
                {
                    "table_name": table["table_name"],
                    "columns": [
                        {"column_name": c["column_name"], "data_type": c.get("data_type", "STRING")}
                        for c in cols
                    ],
                }
            ]
        },
        "qa_validation": "{}",
    }


def _patch_modeling_module():
    """Aplica los mocks ANTES de importar `modeling`."""
    G.run_modeling_pipeline = _fake_pipeline
    RL.llm_rate_limiter = _NoopLimiter()


def _reset_capture() -> None:
    _call_count[0] = 0
    _captured.clear()


async def test_chunk_columns(M) -> None:
    cols = [{"column_name": f"c{i}"} for i in range(37)]
    chunks = M._chunk_columns(cols, 25)
    assert len(chunks) == 2, f"esperaba 2, vi {len(chunks)}"
    assert len(chunks[0]) == 25
    assert len(chunks[1]) == 12
    print(f"  ✓ 37 cols → {[len(c) for c in chunks]}")

    small = [{"column_name": "x"}, {"column_name": "y"}]
    assert M._chunk_columns(small, 25) == [small]
    print("  ✓ 2 cols → 1 chunk")

    assert M._chunk_columns([], 25) == []
    print("  ✓ vacio")


async def test_missing_detection(M) -> None:
    inp = {
        "columns": [
            {"column_name": "agent_id"},
            {"column_name": "agent_name"},
            {"column_name": "audit_enabled"},
        ]
    }
    out = {
        "tables": [
            {
                "columns": [
                    {"column_name": "agent_id"},
                    {"column_name": "Agent_Name"},  # case-insensitive
                ]
            }
        ]
    }
    missing = M._missing_input_columns(inp, out)
    names = [c["column_name"] for c in missing]
    assert names == ["audit_enabled"], names
    print(f"  ✓ detect missing: {names}")


async def test_merge_dedup(M) -> None:
    into = {"columns": [{"column_name": "agent_id"}, {"column_name": "agent_name"}]}
    extra = {"columns": [{"column_name": "audit_enabled"}, {"column_name": "AGENT_ID"}]}
    M._merge_table_columns(into, extra)
    names = [c["column_name"] for c in into["columns"]]
    assert names == ["agent_id", "agent_name", "audit_enabled"], names
    print(f"  ✓ merge sin duplicar: {names}")


async def test_run_one_table_chunking(M) -> None:
    _reset_capture()
    big_table = {
        "table_name": "m_agents_registry",
        "columns": [{"column_name": f"col_{i}", "data_type": "STRING"} for i in range(37)],
    }
    base_input = {"target_engine": "databricks_sql", "user_text": "test"}
    result = await M._run_one_table(None, base_input, big_table, "cid_test", 1)

    tables_out = result["generated_model"]["tables"]
    assert len(tables_out) == 1
    out_cols = tables_out[0]["columns"]
    assert _call_count[0] == 2, f"esperaba 2 chunks, vi {_call_count[0]}"
    assert len(out_cols) == 37, f"esperaba 37 cols mergeadas, vi {len(out_cols)}"
    # Subsets correctos
    assert len(_captured[0]["tables"][0]["columns"]) == 25
    assert len(_captured[1]["tables"][0]["columns"]) == 12
    print(f"  ✓ 37 cols → {_call_count[0]} chunks → {len(out_cols)} cols mergeadas")


async def test_run_one_table_no_chunking(M) -> None:
    _reset_capture()
    small_table = {
        "table_name": "m_model_pricing",
        "columns": [{"column_name": f"col_{i}"} for i in range(7)],
    }
    base_input = {"target_engine": "databricks_sql", "user_text": "test"}
    result = await M._run_one_table(None, base_input, small_table, "cid_test", 1)

    out_cols = result["generated_model"]["tables"][0]["columns"]
    assert _call_count[0] == 1, f"esperaba 1 call (sin chunking), vi {_call_count[0]}"
    assert len(out_cols) == 7
    print(f"  ✓ 7 cols → {_call_count[0]} call (sin chunking) → {len(out_cols)} cols")


async def main() -> None:
    _patch_modeling_module()
    # Importar DESPUÉS del patch para que el módulo modeling vea el mock.
    from api.routes import modeling as M

    # Re-aplicar mocks al módulo modeling (importó referencias).
    M.llm_rate_limiter = RL.llm_rate_limiter

    print("== test_chunk_columns ==")
    await test_chunk_columns(M)
    print("== test_missing_detection ==")
    await test_missing_detection(M)
    print("== test_merge_dedup ==")
    await test_merge_dedup(M)
    print("== test_run_one_table_chunking ==")
    await test_run_one_table_chunking(M)
    print("== test_run_one_table_no_chunking ==")
    await test_run_one_table_no_chunking(M)

    print()
    print("=== TODAS LAS PRUEBAS PASARON ===")


if __name__ == "__main__":
    asyncio.run(main())
