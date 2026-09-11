"""Doc 88 §5-6 — rechazo con motivo obligatorio, rechazo → draft y el
historial de solicitudes (`requests`, un registro por ciclo de envío)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.changesets import service

CS = {"projectId": "p1", "id": "c1", "status": "submitted", "owner": "ana", "reviewers": ["beto"],
      "title": "T", "description": "D", "submittedAt": "2026-09-11T10:00:00+00:00",
      "requests": [{"id": "r1", "cycle": 1, "submittedAt": "2026-09-11T10:00:00+00:00", "submittedBy": "ana",
                    "title": "T", "description": "D", "reviewers": ["beto"], "outcome": "pending"}]}


def test_open_cycle_agrega_el_envio_como_pending():
    history = service.open_cycle({"title": "T0", "reviewers": ["qa"]}, "ana", "2026-09-11T10:00:00+00:00", None, "desc", None)
    (cycle,) = history
    assert (cycle["cycle"], cycle["outcome"], cycle["submittedBy"], cycle["title"], cycle["description"], cycle["reviewers"]) == \
        (1, "pending", "ana", "T0", "desc", ["qa"])
    again = service.open_cycle({"requests": history, "title": "T1"}, "ana", "2026-09-12T10:00:00+00:00", "T2", None, ["x"])
    assert [c["cycle"] for c in again] == [1, 2] and again[-1]["title"] == "T2" and again[-1]["reviewers"] == ["x"]


def test_close_cycle_cierra_el_pending_y_sintetiza_uno_para_docs_legacy():
    closed = service.close_cycle(CS, "rejected", "beto", "falta la PK", decisions={"beto": {"status": "rejected", "at": "x"}})
    assert closed[-1]["outcome"] == "rejected" and closed[-1]["decidedBy"] == "beto" and closed[-1]["note"] == "falta la PK"
    assert closed[-1]["decisions"] == {"beto": {"status": "rejected", "at": "x"}} and closed[-1]["id"] == "r1"
    assert CS["requests"][-1]["outcome"] == "pending"          # no muta la entrada
    legacy = service.close_cycle({"owner": "ana", "title": "L", "reviewers": ["beto"], "submittedAt": "s"}, "withdrawn", "ana")
    assert len(legacy) == 1 and (legacy[0]["cycle"], legacy[0]["outcome"], legacy[0]["submittedBy"], legacy[0]["title"]) == \
        (1, "withdrawn", "ana", "L")


def test_submit_abre_un_ciclo_del_historial(monkeypatch):
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"projectId": "p1", "id": "c1", "status": "draft", "owner": "ana",
                                                "requests": [{"cycle": 1, "outcome": "withdrawn"}]}))
    tr = AsyncMock(return_value={"id": "c1", "status": "submitted"})
    monkeypatch.setattr(service.repository, "transition", tr)
    asyncio.run(service.submit("c1", "ana", title="T", reviewers=["qa"]))
    fields = tr.await_args.args[2]
    assert [c["outcome"] for c in fields["requests"]] == ["withdrawn", "pending"]
    assert fields["requests"][-1]["cycle"] == 2 and fields["requests"][-1]["submittedAt"] == fields["submittedAt"]


def test_withdraw_cierra_el_ciclo_como_withdrawn(monkeypatch):
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value=dict(CS)))
    tr = AsyncMock(return_value={"id": "c1", "status": "draft"})
    monkeypatch.setattr(service.repository, "transition", tr)
    asyncio.run(service.withdraw("c1", "ana"))
    fields = tr.await_args.args[2]
    assert fields["status"] == "draft" and fields["requests"][-1]["outcome"] == "withdrawn"


def test_rechazar_sin_motivo_es_error_antes_de_registrar_nada(monkeypatch):
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value=dict(CS)))
    set_approval = AsyncMock()
    monkeypatch.setattr(service.repository, "set_approval", set_approval)
    with pytest.raises(service.RejectionReasonRequired):
        asyncio.run(service.review("c1", "beto", "reject", "   "))
    set_approval.assert_not_awaited()
    # Un no asignado sigue recibiendo "forbidden" antes que el 422 del motivo.
    assert asyncio.run(service.review("c1", "qa", "reject", None)) == "forbidden"


def test_rechazo_devuelve_la_version_a_draft_con_el_ciclo_cerrado(monkeypatch):
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value=dict(CS)))
    monkeypatch.setattr(service, "ensure_project_alive", AsyncMock())
    decided = {**CS, "approvals": {"beto": {"status": "rejected", "note": "falta la PK", "at": "x"}}}
    monkeypatch.setattr(service.repository, "set_approval", AsyncMock(return_value=decided))
    tr = AsyncMock(return_value={"id": "c1", "status": "draft"})
    monkeypatch.setattr(service.repository, "transition", tr)
    res = asyncio.run(service.review("c1", "beto", "reject", "falta la PK"))
    assert res == {"id": "c1", "status": "draft"}
    cs_id, from_status, fields = tr.await_args.args
    assert (cs_id, from_status) == ("c1", "submitted") and tr.await_args.kwargs == {"expect": {"submittedAt": CS["submittedAt"]}}
    assert (fields["status"], fields["approvals"], fields["submittedAt"], fields["reviewNote"], fields["reviewedBy"]) == \
        ("draft", {}, None, "falta la PK", "beto")
    last = fields["requests"][-1]
    assert (last["outcome"], last["note"], last["decidedBy"], last["decisions"]["beto"]["status"]) == \
        ("rejected", "falta la PK", "beto", "rejected")


def test_aprobacion_cierra_el_ciclo_como_approved(monkeypatch):
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value=dict(CS)))
    monkeypatch.setattr(service, "ensure_project_alive", AsyncMock())
    decided = {**CS, "approvals": {"beto": {"status": "approved", "at": "x"}}}
    monkeypatch.setattr(service.repository, "set_approval", AsyncMock(return_value=decided))
    fin = AsyncMock(return_value={"id": "c1", "status": "approved"})
    monkeypatch.setattr(service, "_apply_and_finalize", fin)
    asyncio.run(service.review("c1", "beto", "approve", None))
    fields = fin.await_args.args[1]
    assert fields["status"] == "approved" and fields["requests"][-1]["outcome"] == "approved"
    assert fields["requests"][-1]["decisions"] == {"beto": {"status": "approved", "at": "x"}}


def test_version_row_expone_el_historial_y_el_ultimo_ciclo():
    row = service.version_row(CS)
    assert row["requests"] == CS["requests"] and row["lastRequest"]["cycle"] == 1
    assert service.version_row({"id": "x", "status": "draft"})["lastRequest"] is None
