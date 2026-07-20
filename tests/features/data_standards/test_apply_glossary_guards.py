"""F2 #1: guards del glosario en standards/apply — locked → 409, added → validate."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.features.data_standards import service
from app.features.data_standards.schemas import ApplyBody, TermEdit


LOCKED = {"id": "t1", "term": "codigo", "abbrev": "COD", "scope": "column",
          "locked": True}


def _mock(monkeypatch, *, before_terms=None, ensure=None):
    monkeypatch.setattr(service.dom_repo, "list_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.dict_repo, "list_entries",
                        AsyncMock(return_value=before_terms or []))
    monkeypatch.setattr(service.udp_repo, "list_udp", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.dict_svc, "ensure_term_valid", ensure or AsyncMock())
    # Mutaciones: NO deben ejecutarse cuando el guard corta.
    for fn in ("delete_entry", "create_entry", "update_entry"):
        monkeypatch.setattr(service.dict_repo, fn, AsyncMock())


def test_apply_editar_termino_locked_409(monkeypatch):
    _mock(monkeypatch, before_terms=[LOCKED])
    body = ApplyBody(termsUpsert=[TermEdit(id="t1", term="codigo", abbrev="CD2",
                                           scope="column")])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.apply("ana", body))
    assert exc.value.status_code == 409
    assert "'codigo'" in exc.value.detail
    service.dict_repo.update_entry.assert_not_awaited()


def test_apply_eliminar_termino_locked_409(monkeypatch):
    _mock(monkeypatch, before_terms=[LOCKED])
    body = ApplyBody(termsDelete=["t1"])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.apply("ana", body))
    assert exc.value.status_code == 409
    service.dict_repo.delete_entry.assert_not_awaited()


def test_apply_termino_agregado_valida_y_propaga_409(monkeypatch):
    ensure = AsyncMock(side_effect=HTTPException(
        status_code=409, detail="El término 'codigo' ... (3 conflictos)."))
    _mock(monkeypatch, ensure=ensure)
    body = ApplyBody(termsUpsert=[TermEdit(term="codigo", abbrev="COD", scope="column")])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.apply("ana", body))
    assert exc.value.status_code == 409
    ensure.assert_awaited_once_with("codigo", "column")
    service.dict_repo.create_entry.assert_not_awaited()


def test_apply_editar_termino_no_locked_no_valida(monkeypatch):
    # Editar un término EXISTENTE no bloqueado no dispara ensure_term_valid
    # (la validación de apply aplica SOLO a los añadidos, según el diseño §1).
    ensure = AsyncMock()
    _mock(monkeypatch, before_terms=[{**LOCKED, "locked": False}], ensure=ensure)
    # apply seguiría hasta las mutaciones; cortamos con un sentinel en set_repo
    # para no mockear todo el pipeline: el guard ya pasó si llega ahí.
    boom = RuntimeError("guard-passed")
    monkeypatch.setattr(service.set_repo, "upsert", AsyncMock(side_effect=boom))
    body = ApplyBody(termsUpsert=[TermEdit(id="t1", term="codigo", abbrev="CD9",
                                           scope="column")],
                     namingConfig={})
    # Sin namingConfig no llega a set_repo; con el update mockeado el flujo sigue
    # — así que solo verificamos que ensure NO fue llamado tras un apply parcial.
    monkeypatch.setattr(service.dict_svc, "rephysicalize",
                        AsyncMock(return_value={"updated": {"tables": 0, "columns": 0}}))
    monkeypatch.setattr(service, "current_snapshot",
                        AsyncMock(return_value={"domains": [], "dict": [], "namingConfig": {}}))
    monkeypatch.setattr(service.repository, "insert_version_next_seq",
                        AsyncMock(return_value={"seq": 1, "label": "v1", "id": "v1"}))
    monkeypatch.setattr(service, "audit", AsyncMock())
    asyncio.run(service.apply("ana", body))
    ensure.assert_not_awaited()
    service.dict_repo.update_entry.assert_awaited_once()


def test_apply_renombre_con_conflicto_409_fail_fast(monkeypatch):
    # Renombre de TEXTO de un término existente: valida con exclude_id y, si hay
    # conflicto, corta antes de mutar nada (fail-fast, decisión owner 2026-07-11).
    ensure = AsyncMock(side_effect=HTTPException(
        status_code=409, detail="El término 'nuevo texto' ... (2 conflictos)."))
    _mock(monkeypatch, before_terms=[{**LOCKED, "locked": False}], ensure=ensure)
    body = ApplyBody(termsUpsert=[TermEdit(id="t1", term="nuevo texto",
                                           abbrev="COD", scope="column")])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.apply("ana", body))
    assert exc.value.status_code == 409
    ensure.assert_awaited_once_with("nuevo texto", "column", exclude_id="t1")
    service.dict_repo.update_entry.assert_not_awaited()
    service.dict_repo.create_entry.assert_not_awaited()
    service.dict_repo.delete_entry.assert_not_awaited()


def test_apply_dos_altas_del_mismo_termino_en_el_batch_409(monkeypatch):
    # I2 (final review): dos ALTAS del mismo término+scope en UN batch pasaban
    # (cada una valida contra el estado PRE-batch) y se escribían ambas — el
    # duplicado exacto que F2 #1 existe para impedir. Seen-set batch-efectivo.
    _mock(monkeypatch)
    body = ApplyBody(termsUpsert=[
        TermEdit(term="codigo", abbrev="COD", scope="column"),
        TermEdit(term=" Codigo ", abbrev="CD2", scope="column"),  # normaliza: strip+lower
    ])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.apply("ana", body))
    assert exc.value.status_code == 409
    assert "codigo" in exc.value.detail.lower()
    service.dict_repo.create_entry.assert_not_awaited()
    service.dict_repo.update_entry.assert_not_awaited()


def test_apply_alta_mas_renombre_al_mismo_termino_409(monkeypatch):
    # I2: alta de X + renombre de un término existente a X en el mismo batch.
    _mock(monkeypatch, before_terms=[{**LOCKED, "locked": False}])  # t1='codigo'
    body = ApplyBody(termsUpsert=[
        TermEdit(term="importe", abbrev="IMP", scope="column"),
        TermEdit(id="t1", term="importe", abbrev="COD", scope="column"),
    ])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.apply("ana", body))
    assert exc.value.status_code == 409
    service.dict_repo.create_entry.assert_not_awaited()
    service.dict_repo.update_entry.assert_not_awaited()


def test_apply_mismo_termino_en_scopes_distintos_no_choca(monkeypatch):
    # La unicidad es POR SCOPE (misma regla que find_glossary_duplicate): dos
    # altas del mismo texto en column y table conviven.
    _mock(monkeypatch)
    monkeypatch.setattr(service.dict_svc, "rephysicalize",
                        AsyncMock(return_value={"updated": {"tables": 0, "columns": 0}}))
    monkeypatch.setattr(service, "current_snapshot",
                        AsyncMock(return_value={"domains": [], "dict": [], "namingConfig": {}}))
    monkeypatch.setattr(service.repository, "insert_version_next_seq",
                        AsyncMock(return_value={"seq": 1, "label": "v1", "id": "v1"}))
    monkeypatch.setattr(service, "audit", AsyncMock())
    body = ApplyBody(termsUpsert=[
        TermEdit(term="codigo", abbrev="COD", scope="column"),
        TermEdit(term="codigo", abbrev="COD", scope="table"),
    ])
    asyncio.run(service.apply("ana", body))
    assert service.dict_repo.create_entry.await_count == 2


def test_apply_edit_sin_cambio_de_texto_no_valida(monkeypatch):
    # Editar SOLO abbrev/wordType (mismo texto) NO dispara ensure_term_valid.
    ensure = AsyncMock()
    _mock(monkeypatch, before_terms=[{**LOCKED, "locked": False}], ensure=ensure)
    body = ApplyBody(termsUpsert=[TermEdit(id="t1", term="codigo", abbrev="CD9",
                                           scope="column")],
                     namingConfig={})
    monkeypatch.setattr(service.dict_svc, "rephysicalize",
                        AsyncMock(return_value={"updated": {"tables": 0, "columns": 0}}))
    monkeypatch.setattr(service, "current_snapshot",
                        AsyncMock(return_value={"domains": [], "dict": [], "namingConfig": {}}))
    monkeypatch.setattr(service.repository, "insert_version_next_seq",
                        AsyncMock(return_value={"seq": 1, "label": "v1", "id": "v1"}))
    monkeypatch.setattr(service, "audit", AsyncMock())
    asyncio.run(service.apply("ana", body))
    ensure.assert_not_awaited()
    service.dict_repo.update_entry.assert_awaited_once()
