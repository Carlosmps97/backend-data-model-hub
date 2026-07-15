"""I3 (final review + refinamiento): rollback × D4 — restaurar un snapshot NO
puede pisar ni eliminar términos HOY bloqueados (bloqueado = intocable para
TODOS hasta que un admin desbloquee; ese unlock queda auditado).

Refinamiento (E2E en vivo): D4 protege el CONTENIDO de la entrada
(term/abbrev/scope/wordType) — los campos de lock NO cuentan en la comparación
y el lock vigente NUNCA se revierte: entrada bloqueada con contenido idéntico
en el snapshot = se PRESERVA (ni upsert ni soft-delete), sin 409."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.features.data_standards import repository as ds_repo, service


LOCKED_NOW = {"id": "t1", "term": "codigo", "abbrev": "COD", "scope": "column",
              "wordType": None, "locked": True, "lockedBy": "admin",
              "lockedAt": "2026-07-11T00:00:00+00:00"}


# ── locked_terms_touched (puro) ────────────────────────────────────────────

def test_locked_ausente_del_snapshot_se_reporta():
    # Snapshot pre-creación del término: restaurar lo soft-deletearía.
    assert service.locked_terms_touched([], [LOCKED_NOW]) == ["codigo"]


def test_locked_contenido_distinto_en_snapshot_se_reporta():
    # El snapshot trae OTRO contenido (abbrev): restaurar pisaría la entrada.
    snap = [{**LOCKED_NOW, "abbrev": "CD-VIEJO", "locked": False,
             "lockedBy": None, "lockedAt": None}]
    assert service.locked_terms_touched(snap, [LOCKED_NOW]) == ["codigo"]


def test_locked_identico_salvo_lock_keys_no_se_reporta():
    # Refinamiento: snapshot PRE-bloqueo con contenido idéntico = no-op (solo
    # difieren locked/lockedBy/lockedAt, que NO cuentan) → sin 409.
    snap = [{**LOCKED_NOW, "locked": False, "lockedBy": None, "lockedAt": None}]
    assert service.locked_terms_touched(snap, [LOCKED_NOW]) == []


def test_locked_identico_en_snapshot_no_se_reporta():
    # Snapshot post-bloqueo con el mismo contenido: el restore es no-op para él.
    assert service.locked_terms_touched([dict(LOCKED_NOW)], [LOCKED_NOW]) == []


def test_termino_no_locked_no_se_reporta():
    # Los NO bloqueados se pisan/eliminan libremente (semántica del restore).
    assert service.locked_terms_touched([], [{**LOCKED_NOW, "locked": False}]) == []


# ── locked_ids_preserved (puro) ────────────────────────────────────────────

def test_preserved_incluye_locked_identico_pre_y_post_bloqueo():
    pre = [{**LOCKED_NOW, "locked": False, "lockedBy": None, "lockedAt": None}]
    assert service.locked_ids_preserved(pre, [LOCKED_NOW]) == {"t1"}
    assert service.locked_ids_preserved([dict(LOCKED_NOW)], [LOCKED_NOW]) == {"t1"}


def test_preserved_excluye_contenido_distinto_ausentes_y_no_locked():
    otro = [{**LOCKED_NOW, "abbrev": "CD-VIEJO"}]
    assert service.locked_ids_preserved(otro, [LOCKED_NOW]) == set()      # → 409
    assert service.locked_ids_preserved([], [LOCKED_NOW]) == set()        # → 409
    assert service.locked_ids_preserved(
        [dict(LOCKED_NOW)], [{**LOCKED_NOW, "locked": False}]) == set()   # restore normal


# ── rollback: 409 solo si TOCA contenido bloqueado ─────────────────────────

def _mock_rollback(monkeypatch, *, snapshot, cur_dict):
    monkeypatch.setattr(service.repository, "get_version",
                        AsyncMock(return_value={"seq": 16, "label": "v16",
                                                "snapshot": snapshot}))
    monkeypatch.setattr(service, "current_snapshot",
                        AsyncMock(return_value={"domains": [], "dict": cur_dict,
                                                "namingConfig": {}, "udp": []}))
    mocks = {name: AsyncMock() for name in
             ("restore_domains", "restore_dict", "restore_naming")}
    for name, m in mocks.items():
        monkeypatch.setattr(service.repository, name, m)
    mocks["restore_udp"] = AsyncMock()
    monkeypatch.setattr(service.udp_repo, "restore_udp", mocks["restore_udp"])
    return mocks


def _mock_rollback_tail(monkeypatch):
    monkeypatch.setattr(service.dict_svc, "rephysicalize",
                        AsyncMock(return_value={"updated": {"tables": 0, "columns": 0}}))
    monkeypatch.setattr(service.dom_svc, "propagate", AsyncMock(return_value={"updated": 0}))
    monkeypatch.setattr(service.repository, "insert_version_next_seq",
                        AsyncMock(return_value={"seq": 17, "label": "v17", "id": "v17",
                                                "kind": "rollback", "revertsSeq": 16}))
    monkeypatch.setattr(service, "audit", AsyncMock())


def test_rollback_que_pisa_contenido_locked_409_sin_restaurar(monkeypatch):
    # (b) El snapshot trae OTRO contenido (abbrev) para el término bloqueado.
    snap = {"domains": [], "namingConfig": {}, "udp": [],
            "dict": [{**LOCKED_NOW, "abbrev": "CD-VIEJO", "locked": False,
                      "lockedBy": None, "lockedAt": None}]}
    mocks = _mock_rollback(monkeypatch, snapshot=snap, cur_dict=[LOCKED_NOW])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.rollback("mr", 16))
    assert exc.value.status_code == 409
    assert "'codigo'" in exc.value.detail
    for m in mocks.values():  # NINGÚN restore debe haberse ejecutado
        m.assert_not_awaited()


def test_rollback_que_eliminaria_termino_locked_409(monkeypatch):
    # (c) El término bloqueado ni existe en el snapshot → el restore lo soft-deletearía.
    snap = {"domains": [], "namingConfig": {}, "udp": [], "dict": []}
    mocks = _mock_rollback(monkeypatch, snapshot=snap, cur_dict=[LOCKED_NOW])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.rollback("mr", 16))
    assert exc.value.status_code == 409
    mocks["restore_dict"].assert_not_awaited()


def test_rollback_snapshot_pre_lock_identico_procede_y_preserva_lock(monkeypatch):
    # (a) Refinamiento: snapshot PRE-bloqueo con contenido idéntico → el
    # rollback PROCEDE y esa entrada se preserva por completo (el restore la
    # salta: ni upsert con locked=False ni soft-delete → sigue bloqueada).
    snap = {"domains": [], "namingConfig": {}, "udp": [],
            "dict": [{**LOCKED_NOW, "locked": False, "lockedBy": None, "lockedAt": None}]}
    mocks = _mock_rollback(monkeypatch, snapshot=snap, cur_dict=[LOCKED_NOW])
    _mock_rollback_tail(monkeypatch)
    v = asyncio.run(service.rollback("mr", 16))
    assert v["seq"] == 17
    mocks["restore_dict"].assert_awaited_once_with(snap["dict"], preserve_ids={"t1"})


def test_rollback_con_locked_identico_post_lock_procede_y_preserva(monkeypatch):
    # (4) Snapshot tomado DESPUÉS del lock (contenido y lock idénticos): el
    # rollback procede y la entrada bloqueada también se preserva tal cual.
    snap = {"domains": [], "namingConfig": {}, "udp": [], "dict": [dict(LOCKED_NOW)]}
    mocks = _mock_rollback(monkeypatch, snapshot=snap, cur_dict=[dict(LOCKED_NOW)])
    _mock_rollback_tail(monkeypatch)
    v = asyncio.run(service.rollback("mr", 16))
    assert v["seq"] == 17
    mocks["restore_dict"].assert_awaited_once_with(snap["dict"], preserve_ids={"t1"})


# ── restore_dict con preserve_ids (repo, fake db) ──────────────────────────

class _FakeDictColl:
    def __init__(self):
        self.update_many_calls: list[tuple] = []
        self.bulk_ops = None

    async def update_many(self, flt, update):
        self.update_many_calls.append((flt, update))

    async def bulk_write(self, ops, ordered=False):
        self.bulk_ops = ops


def test_restore_dict_preserva_entradas_bloqueadas_identicas(monkeypatch):
    coll = _FakeDictColl()
    monkeypatch.setattr(ds_repo, "get_db",
                        AsyncMock(return_value={"glossary_terms": coll}))
    entries = [{"id": "t1", "term": "codigo", "abbrev": "COD", "scope": "column",
                "locked": False},   # pre-bloqueo: NO debe escribirse
               {"id": "t2", "term": "monto", "abbrev": "MTO", "scope": "column"}]
    asyncio.run(ds_repo.restore_dict(entries, preserve_ids={"t1"}))
    flt, _ = coll.update_many_calls[0]
    assert set(flt["_id"]["$nin"]) == {"t1", "t2"}       # t1 NO se soft-deletea
    assert [op._filter["_id"] for op in coll.bulk_ops] == ["t2"]  # t1 sin upsert (lock intacto)
