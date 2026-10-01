"""Doc 105 (A1-o1): un publish cuyo PROCESO murió después del claim deja la
versión `approved` sin `appliedAt`. La recuperación ya no re-aplica a ciegas
(sin gates, sin imágenes previas, sin poda ni cascada): la devuelve a revisión
— como el revert del publish — y el revisor vuelve a aprobar por el camino
normal."""
from __future__ import annotations

import asyncio

from app.features.changesets import repository, service
from scripts import reapply_changeset
from tests.integration.conftest import table_payload


def _stuck(api, world, fake_db):
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs, "canonical_tables", "t-rec", table_payload("M_REC", "Rec"))
    ana.post(f"/api/changesets/{cs}/submit", {"title": "t", "reviewers": ["beto"]})
    header = fake_db.raw["changesets"].find_one({"_id": cs})
    # el proceso murió justo después del claim (como lo deja `review` → `_apply_and_finalize`)
    # hace más de media hora: un publish vivo nunca tarda tanto (ver RECOVER_MIN_AGE_SECONDS)
    fields = {"status": "approved", "reviewedBy": "beto", "reviewedAt": "2026-01-01T00:00:00+00:00",
              "approvals": {"beto": {"status": "approved", "at": "T"}},
              "requests": service.close_cycle({**header, "id": cs}, "approved", "beto",
                                              decisions={"beto": {"status": "approved", "at": "T"}})}
    fake_db.raw["changesets"].update_one({"_id": cs}, {"$set": fields})
    return cs


def test_la_version_trabada_vuelve_a_revision_y_se_publica_normal(api, world, fake_db):
    cs = _stuck(api, world, fake_db)
    out = asyncio.run(reapply_changeset.recover(cs))
    assert [r["id"] for r in out["reverted"]] == [cs] and out["recent"] == []
    h = fake_db.raw["changesets"].find_one({"_id": cs})
    assert h["status"] == "submitted" and h["approvals"] == {} and h["partialApplyAt"]
    assert h["requests"][-1]["outcome"] == "pending" and "decidedAt" not in h["requests"][-1]
    assert fake_db.raw["canonical_tables"].count_documents({"_id": "t-rec"}) == 0      # no aplicó a ciegas
    done = api("beto").post(f"/api/changesets/{cs}/review", {"decision": "approve"})
    assert done["appliedAt"]
    assert fake_db.raw["changeset_changes"].find_one(
        {"_id": repository.change_key(cs, "canonical_tables", "t-rec")})["beforeAt"]     # con imagen previa


def test_sin_versiones_trabadas_no_hace_nada(api, world):
    assert asyncio.run(reapply_changeset.recover(None)) == {"reverted": [], "recent": [], "undated": []}


def _submitted(api, world, tid="t-live"):
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs, "canonical_tables", tid, table_payload("M_LIVE", "Live"))
    ana.post(f"/api/changesets/{cs}/submit", {"title": "t", "reviewers": ["beto"]})
    return cs


def test_no_devuelve_a_revision_un_publish_que_sigue_vivo(api, world, fake_db, monkeypatch):
    """Revisión R1: el script tomaba cualquier approved sin appliedAt; corrido
    mientras el OTRO proceso todavía aplicaba (versión grande), la dejaba
    `submitted` CON `appliedAt`. Un claim reciente se salta (salvo --force)."""
    cs = _submitted(api, world)
    real = repository.apply_changes
    seen: dict = {}

    async def slow_apply(plan, progress=None):
        seen["out"] = await reapply_changeset.recover(cs)          # el operador, en medio del apply
        return await real(plan, progress=progress)

    monkeypatch.setattr(repository, "apply_changes", slow_apply)
    done = api("beto").post(f"/api/changesets/{cs}/review", {"decision": "approve"})
    assert done["status"] == "approved" and done["appliedAt"]
    assert seen["out"]["reverted"] == [] and seen["out"]["recent"] == [cs]


def test_forzado_en_medio_de_un_publish_vivo_no_deja_un_estado_imposible(api, world, fake_db, monkeypatch):
    """Con --force en medio de un apply vivo: el estampado final de `appliedAt`
    va condicionado al claim, así que la versión queda en revisión SIN
    `appliedAt` (y re-aprobarla converge), nunca «submitted con appliedAt»."""
    cs = _submitted(api, world)
    real = repository.apply_changes

    async def slow_apply(plan, progress=None):
        await reapply_changeset.recover(cs, force=True)
        return await real(plan, progress=progress)

    monkeypatch.setattr(repository, "apply_changes", slow_apply)
    status, _ = api("beto").call("POST", f"/api/changesets/{cs}/review", {"decision": "approve"})
    h = fake_db.raw["changesets"].find_one({"_id": cs})
    assert status == 409
    assert h["status"] == "submitted" and not h.get("appliedAt") and h["partialApplyAt"]
    monkeypatch.setattr(repository, "apply_changes", real)
    assert api("beto").post(f"/api/changesets/{cs}/review", {"decision": "approve"})["appliedAt"]


def test_recuperar_va_condicionado_al_claim_leido_y_sin_fecha_legible_no_se_arriesga(monkeypatch):
    from unittest.mock import AsyncMock

    old = {"id": "c1", "status": "approved", "appliedAt": None, "requests": [],
           "reviewedAt": "2026-01-01T00:00:00+00:00"}
    tr = AsyncMock(return_value={"id": "c1", "status": "submitted"})
    monkeypatch.setattr(service.repository, "transition", tr)
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value=old))
    asyncio.run(service.recover_interrupted_publish("c1"))
    # otro claim en el medio (re-aprobado tras otra recuperación) no se revierte
    assert tr.await_args.kwargs["expect"] == {"appliedAt": None, "reviewedAt": old["reviewedAt"]}
    tr.reset_mock()
    monkeypatch.setattr(service.repository, "get", AsyncMock(return_value={**old, "reviewedAt": "T"}))
    assert asyncio.run(service.recover_interrupted_publish("c1")) == "undated"
    tr.assert_not_awaited()
    assert asyncio.run(service.recover_interrupted_publish("c1", min_age_seconds=0))   # --force


def test_estampado_negado_lo_dice_y_queda_auditado(api, world, fake_db, monkeypatch):
    """Ronda 3 (revisor R4): con el apply vivo y un `--force` en el medio, el
    revisor recibía «no longer in review (withdrawn or already decided)» —
    falso: SIGUE en revisión y hay que re-aprobarla— y la aprobación que
    escribió producción no quedaba en la auditoría."""
    cs = _submitted(api, world)
    real = repository.apply_changes

    async def apply_with_forced_recover(plan, progress=None):
        await reapply_changeset.recover(cs, force=True)          # el operador, en el otro proceso
        return await real(plan, progress=progress)

    monkeypatch.setattr(repository, "apply_changes", apply_with_forced_recover)
    status, body = api("beto").call("POST", f"/api/changesets/{cs}/review", {"decision": "approve"})
    assert status == 409
    assert body["detail"] == ("This request was applied, but it was sent back to review while it was being "
                              "applied: approve it again to finish.")
    audit = list(fake_db.raw["audit_log"].find({"action": "changeset.decide", "target": cs}))
    assert len(audit) == 1 and audit[0]["meta"]["result"] == "sent-back"


def test_sin_fecha_legible_el_script_no_manda_a_reintentar(monkeypatch, capsys):
    """Ronda 3 (revisor R4): sin `reviewedAt` legible (cabeceras legadas) el
    script decía «de hace menos de 30 min… reintenta más tarde» — reintentar
    nunca serviría. Ahora lo dice: verificar a mano y usar --force."""
    import sys

    from app.core.db import client as db_client
    from tests.support.fakedb import FakeDb

    fake = FakeDb()
    fake.raw["changesets"].insert_one({"_id": "cs1", "title": "t", "owner": "ana", "projectId": "p",
                                       "status": "approved", "appliedAt": None, "reviewedAt": None,
                                       "requests": []})

    async def connect() -> None:
        monkeypatch.setattr(db_client, "_pg_db", fake)

    async def disconnect() -> None:
        return None

    monkeypatch.setattr(db_client, "connect", connect)
    monkeypatch.setattr(db_client, "disconnect", disconnect)
    monkeypatch.setattr(sys, "argv", ["reapply_changeset.py"])
    asyncio.run(reapply_changeset.main())
    out = capsys.readouterr().out
    assert fake.raw["changesets"].find_one({"_id": "cs1"})["status"] == "approved"
    assert "menos de 30 min" not in out and "sin fecha" in out and "--force" in out, out
