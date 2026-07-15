"""F2 #1: enforcement en el CRUD directo — validate al crear/renombrar + lock."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.features.glossary import service
from app.features.glossary.schemas import AbbreviationBody


LOCKED = {"id": "t1", "term": "codigo", "abbrev": "COD", "scope": "column",
          "wordType": None, "locked": True, "lockedBy": "admin",
          "lockedAt": "2026-07-10T00:00:00+00:00"}
UNLOCKED = {**LOCKED, "locked": False, "lockedBy": None, "lockedAt": None}


def _ok_validation(monkeypatch):
    monkeypatch.setattr(service.repository, "list_entries", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "corpus_conflicts",
                        AsyncMock(return_value=([], 0)))


def test_create_valida_y_crea(monkeypatch):
    _ok_validation(monkeypatch)
    created = AsyncMock(return_value={"id": "t9"})
    monkeypatch.setattr(service.repository, "create_entry", created)
    out = asyncio.run(service.create_entry(AbbreviationBody(term="analisis", abbrev="ANL")))
    assert out == {"id": "t9"}
    created.assert_awaited_once()


def test_create_con_conflicto_409_y_no_crea(monkeypatch):
    monkeypatch.setattr(service.repository, "list_entries",
                        AsyncMock(return_value=[{"id": "t1", "term": "codigo", "abbrev": "COD"}]))
    monkeypatch.setattr(service.repository, "corpus_conflicts",
                        AsyncMock(return_value=([], 0)))
    created = AsyncMock()
    monkeypatch.setattr(service.repository, "create_entry", created)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.create_entry(AbbreviationBody(term="Codigo", abbrev="CD2")))
    assert exc.value.status_code == 409
    created.assert_not_awaited()


def test_update_locked_409_sin_tocar(monkeypatch):
    monkeypatch.setattr(service.repository, "get_entry", AsyncMock(return_value=LOCKED))
    updated = AsyncMock()
    monkeypatch.setattr(service.repository, "update_entry", updated)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.update_entry("t1", AbbreviationBody(term="codigo", abbrev="CD")))
    assert exc.value.status_code == 409
    assert "bloqueado" in exc.value.detail
    updated.assert_not_awaited()


def test_update_mismo_termino_no_revalida(monkeypatch):
    # Cambiar SOLO la abreviatura no dispara la validación de corpus.
    monkeypatch.setattr(service.repository, "get_entry", AsyncMock(return_value=UNLOCKED))
    corpus = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(service.repository, "corpus_conflicts", corpus)
    monkeypatch.setattr(service.repository, "update_entry", AsyncMock(return_value={"id": "t1"}))
    out = asyncio.run(service.update_entry("t1", AbbreviationBody(term="Codigo", abbrev="CD9")))
    assert out == {"id": "t1"}
    corpus.assert_not_awaited()


def test_update_renombre_valida_y_409(monkeypatch):
    monkeypatch.setattr(service.repository, "get_entry", AsyncMock(return_value=UNLOCKED))
    monkeypatch.setattr(service.repository, "list_entries", AsyncMock(return_value=[UNLOCKED]))
    monkeypatch.setattr(service.repository, "corpus_conflicts",
                        AsyncMock(return_value=([{"entity": "table", "tableName": "X",
                                                  "columnName": None,
                                                  "logicalName": "cuenta"}], 1)))
    updated = AsyncMock()
    monkeypatch.setattr(service.repository, "update_entry", updated)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.update_entry("t1", AbbreviationBody(term="cuenta", abbrev="CTA")))
    assert exc.value.status_code == 409
    updated.assert_not_awaited()


def test_update_inexistente_devuelve_none(monkeypatch):
    monkeypatch.setattr(service.repository, "get_entry", AsyncMock(return_value=None))
    assert asyncio.run(service.update_entry("nope", AbbreviationBody(term="x", abbrev="X"))) is None


def test_delete_locked_409(monkeypatch):
    monkeypatch.setattr(service.repository, "get_entry", AsyncMock(return_value=LOCKED))
    deleted = AsyncMock()
    monkeypatch.setattr(service.repository, "delete_entry", deleted)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.delete_entry("t1"))
    assert exc.value.status_code == 409
    deleted.assert_not_awaited()


def test_delete_desbloqueado_pasa(monkeypatch):
    monkeypatch.setattr(service.repository, "get_entry", AsyncMock(return_value=UNLOCKED))
    monkeypatch.setattr(service.repository, "delete_entry", AsyncMock(return_value=True))
    assert asyncio.run(service.delete_entry("t1")) is True
