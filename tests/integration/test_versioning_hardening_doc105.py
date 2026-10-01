"""Doc 105 — endurecimiento del versionado (hallazgos H1, H3, H4, H5 y H7 del
análisis A1, verificados contra el código): cada test falló antes del arreglo.

- H1: renombrar un esquema es TODO o NADA (una escritura condicionada al dueño).
- H3: una versión cuyo publish a medias borró SU proyecto se puede re-aprobar
  (o retirar y re-enviar) y converge; H3b: referencias que la cascada ya borró
  no la traban.
- H4: re-editar tras un publish a medias conserva la imagen previa; un approve
  que falló ANTES de escribir re-captura imágenes frescas.
- H5: una excepción no de negocio en los gates devuelve el request a revisión.
- H7: un draft no publica sobre ids de entidades de OTRO proyecto."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.changesets import repository, service
from tests.integration.conftest import build_world, column_payload, publish, table_payload
from tests.support.fakedb import FakeCollection


def _snap(api, pid, owner="ana"):
    return api(owner).post("/api/changesets/snapshot", {"projectId": pid}, expect=201)["id"]


def _hdr(fake_db, cs_id):
    return fake_db.raw["changesets"].find_one({"_id": cs_id})


def test_h1_rename_schema_es_todo_o_nada_ante_una_transferencia(api, world, fake_db, monkeypatch):
    cs_id = _snap(api, world["pid"])
    real_bulk = FakeCollection.bulk_write
    done = {"x": False}

    async def bulk_con_transfer(self, ops, ordered=True):
        if self.name == "changeset_changes" and not done["x"]:
            done["x"] = True     # la transferencia cae entre el touch y la escritura
            await repository.transfer_owner(cs_id, "ana", "carla",
                                            {"from": "ana", "to": "carla", "by": "admin", "at": "T"})
        return await real_bulk(self, ops, ordered)

    monkeypatch.setattr(FakeCollection, "bulk_write", bulk_con_transfer)
    res = asyncio.run(service.rename_schema(cs_id, "ana", world["schema"], "STG2"))
    assert res == "forbidden"
    assert fake_db.raw["changeset_changes"].count_documents({"csId": cs_id}) == 0   # nada a medias


def test_h1_rename_schema_timeout_no_deja_nada(api, world, fake_db, monkeypatch):
    cs_id = _snap(api, world["pid"])
    real_bulk = FakeCollection.bulk_write

    async def bulk_timeout(self, ops, ordered=True):
        if self.name == "changeset_changes":
            raise TimeoutError("timeout")
        return await real_bulk(self, ops, ordered)

    monkeypatch.setattr(FakeCollection, "bulk_write", bulk_timeout)
    with pytest.raises(TimeoutError):
        asyncio.run(service.rename_schema(cs_id, "ana", world["schema"], "STG2"))
    assert fake_db.raw["changeset_changes"].count_documents({"csId": cs_id}) == 0


def test_h1_rename_schema_feliz_por_http(api, world, fake_db):
    ana = api("ana")
    cs_id = _snap(api, world["pid"])
    out = ana.post(f"/api/changesets/{cs_id}/schemas/{world['schema']}/rename", {"newName": "STG2"})
    assert out == {"tables": 2, "views": 0}
    publish(api, cs_id)
    assert {d["schema"] for d in fake_db.raw["canonical_tables"].find({"projectId": world["pid"]})} == {"STG2"}


def test_h3_reaprobar_termina_el_borrado_del_proyecto(api, world, fake_db, monkeypatch):
    ana, beto = api("ana"), api("beto")
    cs_id = _snap(api, world["pid"])
    ana.change(cs_id, "projects", world["pid"], None, op="delete")
    ana.post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]})
    real_cascade = service.projects_repo.cascade_delete
    monkeypatch.setattr(service.projects_repo, "cascade_delete", AsyncMock(side_effect=TimeoutError("t")))
    with pytest.raises(TimeoutError):
        asyncio.run(service.review(cs_id, "beto", "approve", None))
    monkeypatch.setattr(service.projects_repo, "cascade_delete", real_cascade)
    out = beto.post(f"/api/changesets/{cs_id}/review", {"decision": "approve"})
    assert out["status"] == "approved" and out["appliedAt"]
    assert fake_db.raw["canonical_tables"].count_documents({"projectId": world["pid"], "flgactive": True}) == 0


def test_h3_withdraw_y_reenviar_tambien_converge(api, world, fake_db, monkeypatch):
    ana = api("ana")
    cs_id = _snap(api, world["pid"])
    ana.change(cs_id, "projects", world["pid"], None, op="delete")
    ana.post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]})
    real_cascade = service.projects_repo.cascade_delete
    monkeypatch.setattr(service.projects_repo, "cascade_delete", AsyncMock(side_effect=TimeoutError("t")))
    with pytest.raises(TimeoutError):
        asyncio.run(service.review(cs_id, "beto", "approve", None))
    monkeypatch.setattr(service.projects_repo, "cascade_delete", real_cascade)
    ana.post(f"/api/changesets/{cs_id}/withdraw")
    st, _ = ana.call("PUT", f"/api/changesets/{cs_id}/changes",
                     {"collection": "canonical_tables", "entityId": "t-x", "op": "upsert",
                      "payload": table_payload("M_X", "X")})
    assert st == 409                                   # editar un proyecto borrado sigue bloqueado
    publish(api, cs_id)                                # re-enviar + aprobar sí


def test_h4_before_no_se_contamina_al_reeditar(api, world, fake_db, monkeypatch):
    ana = api("ana")
    cs_id = _snap(api, world["pid"])
    ana.change(cs_id, "canonical_tables", world["t1"], table_payload("M_CLIENTE", "Cliente A"))
    ana.change(cs_id, "canonical_columns", "c-h4", column_payload(world["t1"], "EXTRA", "Extra", 2))
    real_apply = repository.apply_changes

    async def partial(plan, progress=None):
        await real_apply([p for p in plan if p[0] == plan[0][0]], progress=progress)
        raise TimeoutError("timeout a mitad del apply")

    monkeypatch.setattr(repository, "apply_changes", partial)
    ana.post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]})
    with pytest.raises(TimeoutError):
        asyncio.run(service.review(cs_id, "beto", "approve", None))
    monkeypatch.setattr(repository, "apply_changes", real_apply)
    ana.post(f"/api/changesets/{cs_id}/withdraw")
    ana.change(cs_id, "canonical_tables", world["t1"], table_payload("M_CLIENTE", "Cliente B"))
    publish(api, cs_id)
    key = repository.change_key(cs_id, "canonical_tables", world["t1"])
    assert fake_db.raw["changeset_changes"].find_one({"_id": key})["before"]["logicalName"] == "Cliente"
    # la columna nueva (nunca llegó): before=None + beforeAt (no existía) intacto
    ck = fake_db.raw["changeset_changes"].find_one({"_id": repository.change_key(cs_id, "canonical_columns", "c-h4")})
    assert ck["before"] is None and ck["beforeAt"]
    draft = ana.post(f"/api/changesets/{world['cs2']}/rollback")
    inv = fake_db.raw["changeset_changes"].find_one(
        {"_id": repository.change_key(draft["id"], "canonical_tables", world["t1"])})
    assert inv["payload"]["logicalName"] == "Cliente"


def test_h4_sin_escritura_la_imagen_se_recaptura_fresca(api, world, fake_db, monkeypatch):
    """Approve que falla ANTES de escribir (sólo estampó) + otra versión publica
    sobre la misma tabla en el medio: el reintento debe re-capturar (hoy la
    marca vieja queda y el rollback desharía también la otra versión)."""
    ana, carla = api("ana"), api("carla")
    cs_id = _snap(api, world["pid"])
    ana.change(cs_id, "canonical_tables", world["t1"], table_payload("M_CLIENTE", "Cliente A"))
    real_apply = repository.apply_changes
    monkeypatch.setattr(repository, "apply_changes", AsyncMock(side_effect=TimeoutError("antes de escribir")))
    ana.post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]})
    with pytest.raises(TimeoutError):
        asyncio.run(service.review(cs_id, "beto", "approve", None))
    monkeypatch.setattr(repository, "apply_changes", real_apply)
    other = _snap(api, world["pid"], owner="carla")
    carla.change(other, "canonical_tables", world["t1"], table_payload("M_CLIENTE", "Cliente C"))
    publish(api, other, owner="carla")
    publish_again = api("beto").post(f"/api/changesets/{cs_id}/review", {"decision": "approve"})
    assert publish_again["appliedAt"]
    key = repository.change_key(cs_id, "canonical_tables", world["t1"])
    assert fake_db.raw["changeset_changes"].find_one({"_id": key})["before"]["logicalName"] == "Cliente C"


def test_h5_timeout_en_gate_devuelve_a_revision(api, world, fake_db, monkeypatch):
    ana = api("ana")
    cs_id = _snap(api, world["pid"])
    ana.change(cs_id, "canonical_tables", "t-h5", table_payload("M_H5", "H5"))
    ana.post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]})
    real = service._publish_duplicate_items
    monkeypatch.setattr(service, "_publish_duplicate_items", AsyncMock(side_effect=TimeoutError("t")))
    with pytest.raises(TimeoutError):
        asyncio.run(service.review(cs_id, "beto", "approve", None))
    h = _hdr(fake_db, cs_id)
    assert h["status"] == "submitted" and h["approvals"] == {} and not h.get("partialApplyAt")
    monkeypatch.setattr(service, "_publish_duplicate_items", real)
    assert api("beto").post(f"/api/changesets/{cs_id}/review", {"decision": "approve"})["appliedAt"]


def test_h7_no_se_publica_sobre_ids_de_otro_proyecto(api, fake_db):
    a = build_world(api, name="Proyecto A", prefix="a")
    b = build_world(api, name="Proyecto B", prefix="b")
    ana = api("ana")
    cs_id = _snap(api, b["pid"])
    ana.change(cs_id, "canonical_tables", a["t1"], table_payload("M_ROBADA", "Robada"))
    ana.change(cs_id, "canonical_tables", a["t2"], None, op="delete")
    ana.post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]})
    st, err = api("beto").call("POST", f"/api/changesets/{cs_id}/review", {"decision": "approve"})
    assert st == 409 and "another project" in str(err)
    assert fake_db.raw["canonical_tables"].find_one({"_id": a["t1"]})["projectId"] == a["pid"]
    assert fake_db.raw["canonical_tables"].find_one({"_id": a["t2"]})["flgactive"] is True
    assert _hdr(fake_db, cs_id)["status"] == "submitted"


def test_h3_residual_gate_de_referencias(api, world, fake_db, monkeypatch):
    ana, beto = api("ana"), api("beto")
    cs_id = api("ana").post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs_id, "canonical_columns", "c-res", column_payload(world["t1"], "NUEVA", "Nueva", 5))
    ana.change(cs_id, "projects", world["pid"], None, op="delete")
    ana.post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]})
    real = FakeCollection.update_many

    async def falla_en_ddl_rules(self, flt, upd):
        if self.name == "ddl_rules":
            raise TimeoutError("timeout en la cascada")
        return await real(self, flt, upd)

    monkeypatch.setattr(FakeCollection, "update_many", falla_en_ddl_rules)
    with pytest.raises(TimeoutError):
        asyncio.run(service.review(cs_id, "beto", "approve", None))
    monkeypatch.setattr(FakeCollection, "update_many", real)
    assert fake_db.raw["canonical_tables"].find_one({"_id": world["t1"]})["flgactive"] is False
    out = beto.post(f"/api/changesets/{cs_id}/review", {"decision": "approve"})   # converge
    assert out["status"] == "approved" and out["appliedAt"]


def test_h1_rename_schema_no_queda_a_medias_si_la_transferencia_cae_entre_escrituras(api, world, fake_db, monkeypatch):
    """La transferencia llega DESPUÉS de una escritura completa del rename: si
    el rename fuera de varias escrituras, las siguientes caerían y quedaría el
    esquema renombrado sin sus tablas. Todo o nada: 0 cambios o todos."""
    cs_id = _snap(api, world["pid"])
    real = repository.set_changes_bulk
    state = {"calls": 0}

    async def bulk_then_transfer(cs, items, owner=None):
        out = await real(cs, items, owner=owner)
        state["calls"] += 1
        if state["calls"] == 1:
            await repository.transfer_owner(cs_id, "ana", "carla",
                                            {"from": "ana", "to": "carla", "by": "admin", "at": "T"})
        return out

    monkeypatch.setattr(repository, "set_changes_bulk", bulk_then_transfer)
    asyncio.run(service.rename_schema(cs_id, "ana", world["schema"], "STG2"))
    n = fake_db.raw["changeset_changes"].count_documents({"csId": cs_id})
    assert n in (0, 3), n                     # esquema + 2 tablas, o nada


def test_h3_el_draft_que_borro_su_proyecto_a_medias_se_puede_transferir(api, world, fake_db, monkeypatch):
    """Revisión R1: `transfer_version` no aplicaba la salvedad de H3. Un draft
    cuyo publish a medias borró SU proyecto (luego retirado) no se podía
    transferir: si su dueño se fue, nadie más podía terminar el borrado."""
    ana = api("ana")
    cs_id = _snap(api, world["pid"])
    ana.change(cs_id, "projects", world["pid"], None, op="delete")
    ana.post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]})
    real_cascade = service.projects_repo.cascade_delete
    monkeypatch.setattr(service.projects_repo, "cascade_delete", AsyncMock(side_effect=TimeoutError("t")))
    with pytest.raises(TimeoutError):
        asyncio.run(service.review(cs_id, "beto", "approve", None))
    monkeypatch.setattr(service.projects_repo, "cascade_delete", real_cascade)
    ana.post(f"/api/changesets/{cs_id}/withdraw")
    assert _hdr(fake_db, cs_id)["status"] == "draft" and _hdr(fake_db, cs_id)["partialApplyAt"]
    out = api("admin").post(f"/api/changesets/{cs_id}/transfer", {"to": "carla"})
    assert out["owner"] == "carla"
    # …y quien lo recibe termina el borrado por el camino normal
    api("carla").post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]})
    assert api("beto").post(f"/api/changesets/{cs_id}/review", {"decision": "approve"})["appliedAt"]


def test_h3_un_draft_comun_de_un_proyecto_borrado_sigue_sin_transferirse(api, world, fake_db):
    other = _snap(api, world["pid"])
    cs_id = _snap(api, world["pid"])
    api("ana").change(cs_id, "projects", world["pid"], None, op="delete")
    publish(api, cs_id)                                   # el proyecto se borra entero
    st, body = api("admin").call("POST", f"/api/changesets/{other}/transfer", {"to": "carla"})
    assert st == 409 and "deleted" in str(body).lower(), (st, body)
