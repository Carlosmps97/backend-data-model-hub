"""F2 #1: validate_term / ensure_term_valid (repos mockeados, sin DB)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.features.glossary import service


HIT = {"entity": "column", "tableName": "CTA_SOLES", "columnName": "CODIGOCTAPEN",
       "logicalName": "codigo cuenta en soles"}


def test_validate_term_junta_duplicado_y_corpus(monkeypatch):
    monkeypatch.setattr(service.repository, "list_entries",
                        AsyncMock(return_value=[{"id": "t1", "term": "Codigo", "abbrev": "COD"}]))
    monkeypatch.setattr(service.repository, "corpus_conflicts",
                        AsyncMock(return_value=([HIT], 3)))
    out = asyncio.run(service.validate_term("codigo", "column"))
    assert out["ok"] is False
    assert out["conflicts"]["glossaryDuplicate"] == {"id": "t1", "term": "Codigo", "abbrev": "COD"}
    assert out["conflicts"]["corpus"] == [HIT]
    assert out["conflicts"]["total"] == 4  # 3 del corpus + 1 duplicado
    # El repo recibió el patrón de frase completa del término escapado.
    pattern = service.repository.corpus_conflicts.await_args.args[0]
    assert pattern == service.corpus_regex("codigo")
    # Y el listado de términos se pidió del scope correcto.
    service.repository.list_entries.assert_awaited_once_with("column")


def test_validate_term_sin_conflictos_ok(monkeypatch):
    monkeypatch.setattr(service.repository, "list_entries", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "corpus_conflicts",
                        AsyncMock(return_value=([], 0)))
    out = asyncio.run(service.validate_term("codigo de analisis", "column"))
    assert out == {"ok": True,
                   "conflicts": {"glossaryDuplicate": None, "corpus": [], "total": 0}}


def test_ensure_term_valid_levanta_409_con_termino_y_total(monkeypatch):
    monkeypatch.setattr(service.repository, "list_entries", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "corpus_conflicts",
                        AsyncMock(return_value=([HIT], 3)))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.ensure_term_valid("codigo", "column"))
    assert exc.value.status_code == 409
    assert "'codigo'" in exc.value.detail
    assert "3 conflictos" in exc.value.detail


def test_ensure_term_valid_pasa_sin_conflictos(monkeypatch):
    monkeypatch.setattr(service.repository, "list_entries", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "corpus_conflicts",
                        AsyncMock(return_value=([], 0)))
    asyncio.run(service.ensure_term_valid("codigo de analisis", "column"))  # no levanta
