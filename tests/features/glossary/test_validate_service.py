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
    out = asyncio.run(service.validate_term("p1", "codigo", "column"))
    assert out["ok"] is False
    assert out["conflicts"]["glossaryDuplicate"] == {"id": "t1", "term": "Codigo", "abbrev": "COD"}
    assert out["conflicts"]["corpus"] == [HIT]
    assert out["conflicts"]["total"] == 4  # 3 del corpus + 1 duplicado
    # El repo recibió el patrón de frase completa del término escapado.
    pattern = service.repository.corpus_conflicts.await_args.args[1]
    assert pattern == service.corpus_regex("codigo")
    # Doc 94 D9: el corpus se busca SOLO en el scope del término.
    assert service.repository.corpus_conflicts.await_args.args[2] == "column"
    # Y el listado de términos se pidió del scope correcto.
    service.repository.list_entries.assert_awaited_once_with("p1", "column")


def test_validate_term_sin_conflictos_ok(monkeypatch):
    monkeypatch.setattr(service.repository, "list_entries", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "corpus_conflicts",
                        AsyncMock(return_value=([], 0)))
    out = asyncio.run(service.validate_term("p1", "codigo de analisis", "column"))
    assert out == {"ok": True,
                   "conflicts": {"glossaryDuplicate": None, "corpus": [], "total": 0}}


def test_ensure_term_valid_levanta_409_con_termino_y_total(monkeypatch):
    monkeypatch.setattr(service.repository, "list_entries", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "corpus_conflicts",
                        AsyncMock(return_value=([HIT], 3)))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.ensure_term_valid("p1", "codigo", "column"))
    assert exc.value.status_code == 409
    assert "'codigo'" in exc.value.detail
    # Doc 94 D8: mensaje en inglés (UI) y claro sobre QUÉ nombres mira.
    assert exc.value.detail == ("The term 'codigo' can't be added: it already exists in the glossary or "
                                "appears as a full phrase in logical column names (3 conflicts). "
                                "Adding it would rename those columns.")


def test_ensure_term_valid_pasa_sin_conflictos(monkeypatch):
    monkeypatch.setattr(service.repository, "list_entries", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.repository, "corpus_conflicts",
                        AsyncMock(return_value=([], 0)))
    asyncio.run(service.ensure_term_valid("p1", "codigo de analisis", "column"))  # no levanta


# ── Doc 94 D9 · corpus por scope (repositorio con una BD falsa) ──────────

def _fake_db(monkeypatch):
    from unittest.mock import MagicMock
    from app.features.glossary import repository as repo
    tables, columns = MagicMock(), MagicMock()
    tables.count_documents = AsyncMock(return_value=5)
    columns.count_documents = AsyncMock(return_value=2)
    col_cur = MagicMock()
    col_cur.limit.return_value.to_list = AsyncMock(return_value=[
        {"physicalName": "CODCTA", "logicalName": "codigo cuenta", "tableId": "tb1"}])
    columns.find.return_value = col_cur
    tab_cur = MagicMock()
    tab_cur.limit.return_value.to_list = AsyncMock(return_value=[
        {"_id": "tb9", "physicalName": "CODIGOS", "logicalName": "codigo maestro"}])
    tab_cur.to_list = AsyncMock(return_value=[{"_id": "tb1", "physicalName": "CUENTAS"}])
    tables.find.return_value = tab_cur
    monkeypatch.setattr(repo, "get_db", AsyncMock(return_value={repo.TABLES_COLL: tables, repo.COLUMNS_COLL: columns}))
    return repo, tables, columns


def test_corpus_de_un_termino_de_columna_solo_mira_columnas(monkeypatch):
    repo, tables, _ = _fake_db(monkeypatch)
    sample, total = asyncio.run(repo.corpus_conflicts("p1", "x", "column"))
    assert total == 2
    tables.count_documents.assert_not_awaited()
    assert sample == [{"entity": "column", "tableName": "CUENTAS", "columnName": "CODCTA",
                       "logicalName": "codigo cuenta"}]


def test_corpus_de_un_termino_de_tabla_solo_mira_tablas(monkeypatch):
    repo, _, columns = _fake_db(monkeypatch)
    sample, total = asyncio.run(repo.corpus_conflicts("p1", "x", "table"))
    assert total == 5
    columns.count_documents.assert_not_awaited()
    assert sample == [{"entity": "table", "tableName": "CODIGOS", "columnName": None,
                       "logicalName": "codigo maestro"}]
