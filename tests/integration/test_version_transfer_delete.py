"""Doc 104 — transferir el ownership de un draft y eliminar un draft, por HTTP
con la app REAL (routers, services, repositorios, RBAC) sobre la BD en memoria.

Reglas: sólo versiones en draft (una publicada es historia; una en revisión se
retira primero); las administra su owner (con `model.edit`) o un administrador
(`admin.manage`); el destino de una transferencia es un usuario activo cuyo
rol edita modelos. El dueño final es el autor de lo publicado y la versión
recuerda quién la inició."""
from __future__ import annotations

import asyncio
import time

from app.features.changesets import repository, service
from tests.integration.conftest import column_payload, publish, table_payload
from tests.support.fakedb import FakeCollection


def _draft(api, w, owner: str = "ana") -> dict:
    """Draft de `owner` con una tabla nueva `t-transfer` (M_TRANSFER)."""
    cs = api(owner).post("/api/changesets/snapshot", {"projectId": w["pid"]}, expect=201)
    api(owner).change(cs["id"], "canonical_tables", "t-transfer", table_payload("M_TRANSFER", "Transfer"))
    return cs


def _transfer(api, user: str, cs_id: str, body: dict, expect: int = 200):
    return api(user).post(f"/api/changesets/{cs_id}/transfer", body, expect=expect)


# ── Transferir ────────────────────────────────────────────────────────────


def test_owner_transfiere_su_draft_y_el_nuevo_owner_lo_edita_y_publica(api, world):
    ana, carla = api("ana"), api("carla")
    cs_id = _draft(api, world)["id"]

    out = _transfer(api, "ana", cs_id, {"to": "carla", "note": "  vacaciones "})
    assert out["owner"] == "carla" and out["status"] == "draft"
    [t] = out["transfers"]
    assert (t["from"], t["to"], t["by"], t["note"]) == ("ana", "carla", "ana", "vacaciones") and t["at"]

    # La working copy ahora es de carla: ella la edita; ana ya no la edita ni la ve.
    carla.change(cs_id, "canonical_tables", "t-carla", table_payload("M_CARLA", "Carla"))
    ana.change(cs_id, "canonical_tables", "t-ana", table_payload("M_ANA", "Ana"), expect=403)
    ana.get(f"/api/changesets/{cs_id}/effective/canonical_tables", expect=403)
    names = {t["physicalName"] for t in carla.get(f"/api/changesets/{cs_id}/effective/canonical_tables")}
    assert {"M_TRANSFER", "M_CARLA"} <= names and "M_ANA" not in names
    row = next(r for r in carla.get("/api/versions") if r["id"] == cs_id)
    assert row["owner"] == "carla" and [x["from"] for x in row["transfers"]] == ["ana"]

    # Sólo el dueño actual la envía; publicada, carla es la autora y ana «la inició».
    ana.post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]}, expect=403)
    publish(api, cs_id, owner="carla")
    for tid in ("t-transfer", "t-carla"):
        [ev] = carla.get(f"/api/changesets/history/canonical_tables/{tid}")["items"]
        assert ev["user"]["id"] == "carla" and ev["startedBy"]["id"] == "ana", tid


def test_historial_sin_transferencias_no_trae_started_by(api, world):
    items = api("ana").get(f"/api/changesets/history/canonical_tables/{world['t1']}")["items"]
    assert items[0]["user"]["id"] == "ana" and items[0]["startedBy"] is None


def test_admin_administra_un_draft_ajeno_y_puede_asignarselo(api, world):
    cs_id = _draft(api, world)["id"]
    out = _transfer(api, "admin", cs_id, {"to": "admin"})
    assert out["owner"] == "admin" and out["transfers"][0]["by"] == "admin"
    api("admin").change(cs_id, "canonical_tables", "t-admin", table_payload("M_ADMIN", "Admin"))
    api("ana").change(cs_id, "canonical_tables", "t-ana", table_payload("M_ANA", "Ana"), expect=403)
    # Y puede devolverla (otra transferencia, que se acumula en la historia).
    back = _transfer(api, "admin", cs_id, {"to": "ana"})
    assert back["owner"] == "ana" and [(x["from"], x["to"]) for x in back["transfers"]] == [("ana", "admin"), ("admin", "ana")]


def test_solo_el_owner_o_un_admin_transfieren(api, world):
    cs_id = _draft(api, world)["id"]
    _transfer(api, "carla", cs_id, {"to": "carla"}, expect=403)     # modelador ajeno
    _transfer(api, "beto", cs_id, {"to": "carla"}, expect=403)      # revisor ajeno
    _transfer(api, "diego", cs_id, {"to": "carla"}, expect=403)     # lector
    assert api("ana").get(f"/api/changesets/{cs_id}")["owner"] == "ana"


def test_destino_invalido(api, world, fake_db):
    cs_id = _draft(api, world)["id"]
    _transfer(api, "ana", cs_id, {"to": "ana"}, expect=400)         # ya es la dueña
    _transfer(api, "ana", cs_id, {"to": "beto"}, expect=400)        # revisor: su rol no edita modelos
    _transfer(api, "ana", cs_id, {"to": "diego"}, expect=400)       # lector
    _transfer(api, "ana", cs_id, {"to": "nadie"}, expect=400)       # no existe
    fake_db.raw["users"].update_one({"_id": "carla"}, {"$set": {"status": "disabled"}})
    _transfer(api, "ana", cs_id, {"to": "carla"}, expect=400)       # deshabilitada
    _transfer(api, "ana", cs_id, {"to": ""}, expect=422)
    _transfer(api, "ana", cs_id, {"to": "admin", "note": "x" * 501}, expect=422)
    detail = fake_db.raw["changesets"].find_one({"_id": cs_id})
    assert detail["owner"] == "ana" and not detail.get("transfers")


# ── Eliminar ──────────────────────────────────────────────────────────────


def test_owner_elimina_su_draft_con_todos_sus_cambios(api, world, fake_db):
    ana = api("ana")
    cs = _draft(api, world)
    cs_id = cs["id"]
    ana.bulk(cs_id, [
        {"collection": "canonical_columns", "entityId": "c-tr-1", "op": "upsert",
         "payload": column_payload("t-transfer", "COD", "Codigo", 0, pk=True)},
        {"collection": "canonical_columns", "entityId": "c-tr-2", "op": "upsert",
         "payload": column_payload("t-transfer", "NBR", "Nombre", 1)},
    ])
    assert fake_db.raw["changeset_changes"].count_documents({"csId": cs_id}) == 3

    out = ana.delete(f"/api/changesets/{cs_id}")
    assert out == {"id": cs_id, "deleted": True, "versionLabel": cs["versionLabel"], "title": cs["title"],
                   "owner": "ana", "projectId": world["pid"], "changes": 3}
    assert fake_db.raw["changesets"].count_documents({"_id": cs_id}) == 0
    assert fake_db.raw["changeset_changes"].count_documents({"csId": cs_id}) == 0
    assert cs_id not in {r["id"] for r in ana.get("/api/versions")}
    # Producción no cambia.
    tables = ana.get(f"/api/projects/{world['pid']}/catalog/tables")
    assert {t["physicalName"] for t in tables} == {"M_CLIENTE", "M_CUENTA"}


def test_una_sesion_vieja_sobre_un_draft_eliminado_recibe_404_nunca_un_exito_falso(api, world, fake_db):
    ana = api("ana")
    cs_id = _draft(api, world)["id"]
    ana.delete(f"/api/changesets/{cs_id}")

    ana.get(f"/api/changesets/{cs_id}", expect=404)
    ana.get(f"/api/changesets/{cs_id}/diff", expect=404)
    ana.change(cs_id, "canonical_tables", "t-late", table_payload("M_LATE", "Late"), expect=404)
    ana.bulk(cs_id, [{"collection": "canonical_tables", "entityId": "t-late", "op": "upsert",
                      "payload": table_payload("M_LATE", "Late")}], expect=404)
    ana.get(f"/api/changesets/{cs_id}/effective/canonical_tables", expect=404)
    ana.get(f"/api/subject-areas/{world['canvas']}/diagram?changesetId={cs_id}", expect=404)
    ana.get(f"/api/projects/{world['pid']}/catalog/inventory?changesetId={cs_id}", expect=404)
    ana.post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]}, expect=404)
    ana.post(f"/api/changesets/{cs_id}/reopen", expect=404)
    ana.delete(f"/api/changesets/{cs_id}", expect=404)
    _transfer(api, "ana", cs_id, {"to": "carla"}, expect=404)
    # Las escrituras tardías no dejaron cambios huérfanos.
    assert fake_db.raw["changeset_changes"].count_documents({"csId": cs_id}) == 0


def test_admin_elimina_un_draft_ajeno_y_nadie_mas(api, world):
    cs_id = _draft(api, world)["id"]
    api("carla").delete(f"/api/changesets/{cs_id}", expect=403)
    api("beto").delete(f"/api/changesets/{cs_id}", expect=403)
    api("diego").delete(f"/api/changesets/{cs_id}", expect=403)
    assert api("admin").delete(f"/api/changesets/{cs_id}")["owner"] == "ana"


def test_solo_drafts_se_transfieren_o_eliminan(api, world):
    ana, admin = api("ana"), api("admin")
    cs_id = _draft(api, world)["id"]
    ana.post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]})
    _transfer(api, "ana", cs_id, {"to": "carla"}, expect=409)       # en revisión: retirar primero
    ana.delete(f"/api/changesets/{cs_id}", expect=409)
    admin.delete(f"/api/changesets/{cs_id}", expect=409)
    assert ana.get(f"/api/changesets/{cs_id}")["status"] == "submitted"
    # Publicadas (v2 y el marcador v1): nunca.
    for published in (world["cs2"], world["v1"]):
        _transfer(api, "admin", published, {"to": "carla"}, expect=409)
        admin.delete(f"/api/changesets/{published}", expect=409)
    assert {v["id"] for v in ana.get(f"/api/projects/{world['pid']}/versions")} >= {world["cs2"], world["v1"], cs_id}


def test_un_rechazado_legacy_se_elimina_pero_no_se_transfiere(api, world, fake_db):
    cs_id = _draft(api, world)["id"]
    fake_db.raw["changesets"].update_one({"_id": cs_id}, {"$set": {"status": "rejected"}})
    _transfer(api, "ana", cs_id, {"to": "carla"}, expect=409)
    assert api("ana").delete(f"/api/changesets/{cs_id}")["deleted"] is True


def test_auditoria_de_transferir_y_eliminar(api, world, fake_db):
    cs = _draft(api, world)
    _transfer(api, "ana", cs["id"], {"to": "carla", "note": "vacaciones"})
    api("admin").delete(f"/api/changesets/{cs['id']}")
    entries = {e["action"]: e for e in fake_db.raw["audit_log"].find({"target": cs["id"]})}
    tr = entries["changeset.transfer"]
    assert tr["actor"] == "ana" and (tr["meta"]["from"], tr["meta"]["to"], tr["meta"]["note"]) == ("ana", "carla", "vacaciones")
    de = entries["changeset.delete"]
    assert de["actor"] == "admin" and de["meta"]["owner"] == "carla" and de["meta"]["versionLabel"] == cs["versionLabel"]
    assert de["meta"]["changes"] == 1


# ── Concurrencia ──────────────────────────────────────────────────────────


def _upload_lock(cs_id: str, job: str = "job-otro-proceso", owner: str = "ana") -> None:
    """Doc 105: una carga Excel de OTRO proceso de uvicorn está escribiendo en
    la versión (tomó el lock de la cabecera)."""
    assert asyncio.run(repository.claim_upload_lock(cs_id, owner, job)) is not None


def _submit(api, cs_id: str, user: str = "ana", expect: int = 200):
    return api(user).post(f"/api/changesets/{cs_id}/submit", {"title": "t", "reviewers": ["beto"]}, expect=expect)


def test_carga_excel_escribiendose_bloquea_transferir_eliminar_y_enviar(api, world):
    """Doc 105: el lock es de la cabecera (lo ven los dos procesos), no de la
    memoria del proceso que corre la carga."""
    cs_id = _draft(api, world)["id"]
    _upload_lock(cs_id)
    assert "Excel upload" in _transfer(api, "ana", cs_id, {"to": "carla"}, expect=409)["detail"]
    assert "Excel upload" in api("admin").call("DELETE", f"/api/changesets/{cs_id}", expect=409)[1]["detail"]
    assert "Excel upload" in _submit(api, cs_id, expect=409)["detail"]
    asyncio.run(repository.release_upload_lock(cs_id, "job-otro-proceso"))
    assert _transfer(api, "ana", cs_id, {"to": "carla"})["owner"] == "carla"


def test_lock_de_una_carga_cuyo_proceso_murio_no_bloquea(api, world, monkeypatch):
    cs_id = _draft(api, world)["id"]
    _upload_lock(cs_id)
    later = time.time() + repository.UPLOAD_LOCK_STALE_SECONDS + 5
    monkeypatch.setattr(repository, "_clock", lambda: later)
    assert _transfer(api, "ana", cs_id, {"to": "carla"})["owner"] == "carla"


def _stale_get(monkeypatch, concurrent):
    """La lectura del service devuelve la cabecera ANTERIOR a una acción que
    otra sesión completó en el medio (carrera entre leer y escribir). Devuelve
    la función para restaurar la lectura real (`monkeypatch.undo()` desharía
    también la BD en memoria del fixture)."""
    real_get = service.repository.get

    async def stale(cs_id: str):
        before = await real_get(cs_id)
        await concurrent(cs_id)
        return before

    monkeypatch.setattr(service.repository, "get", stale)
    return lambda: monkeypatch.setattr(service.repository, "get", real_get)


def test_carrera_una_carga_toma_la_version_mientras_se_transfiere(api, world, monkeypatch):
    """La lectura del service no ve el lock (lo tomó otro proceso en el medio):
    la ESCRITURA condicionada debe negarse igual."""
    cs_id = _draft(api, world)["id"]
    restore = _stale_get(monkeypatch, lambda cid: repository.claim_upload_lock(cid, "ana", "job-b"))
    out = _transfer(api, "ana", cs_id, {"to": "carla"}, expect=409)
    restore()
    assert "Excel upload" in out["detail"]
    assert asyncio.run(repository.get(cs_id))["owner"] == "ana"


def test_carrera_una_carga_toma_la_version_mientras_se_elimina(api, world, monkeypatch):
    cs_id = _draft(api, world)["id"]
    restore = _stale_get(monkeypatch, lambda cid: repository.claim_upload_lock(cid, "ana", "job-b"))
    status, out = api("admin").call("DELETE", f"/api/changesets/{cs_id}")
    restore()
    assert status == 409 and "Excel upload" in out["detail"]
    assert asyncio.run(repository.get(cs_id)) is not None


def test_carrera_una_carga_toma_la_version_mientras_se_envia(api, world, monkeypatch):
    cs_id = _draft(api, world)["id"]
    restore = _stale_get(monkeypatch, lambda cid: repository.claim_upload_lock(cid, "ana", "job-b"))
    out = _submit(api, cs_id, expect=409)
    restore()
    assert "Excel upload" in out["detail"]
    assert asyncio.run(repository.get(cs_id))["status"] == "draft"


def test_carrera_la_version_se_envia_a_revision_mientras_se_transfiere(api, world, monkeypatch):
    cs_id = _draft(api, world)["id"]
    restore = _stale_get(monkeypatch, lambda cid: repository.transition(cid, "draft", {"status": "submitted"}))
    _transfer(api, "ana", cs_id, {"to": "carla"}, expect=409)
    restore()
    doc = api("ana").get(f"/api/changesets/{cs_id}")
    assert doc["owner"] == "ana" and doc["status"] == "submitted" and not doc.get("transfers")


def test_carrera_otra_sesion_transfiere_antes_de_eliminar(api, world, monkeypatch):
    cs_id = _draft(api, world)["id"]
    entry = service.transfer_entry("ana", "carla", "admin", None)
    restore = _stale_get(monkeypatch, lambda cid: repository.transfer_owner(cid, "ana", "carla", entry))
    api("ana").delete(f"/api/changesets/{cs_id}", expect=409)       # ana ya no es la dueña: no borra lo de carla
    restore()
    assert api("carla").get(f"/api/changesets/{cs_id}")["owner"] == "carla"


def test_carrera_otra_sesion_ya_la_transfirio(api, world, monkeypatch):
    cs_id = _draft(api, world)["id"]
    entry = service.transfer_entry("ana", "carla", "ana", None)
    restore = _stale_get(monkeypatch, lambda cid: repository.transfer_owner(cid, "ana", "carla", entry))
    _transfer(api, "admin", cs_id, {"to": "admin"}, expect=409)     # decidido sobre una lectura vieja
    restore()
    doc = api("admin").get(f"/api/changesets/{cs_id}")
    assert doc["owner"] == "carla" and [x["to"] for x in doc["transfers"]] == ["carla"]


def test_carrera_la_version_se_envia_a_revision_mientras_se_elimina(api, world, monkeypatch, fake_db):
    cs_id = _draft(api, world)["id"]
    restore = _stale_get(monkeypatch, lambda cid: repository.transition(cid, "draft", {"status": "submitted"}))
    api("ana").delete(f"/api/changesets/{cs_id}", expect=409)       # el request que ganó queda intacto
    restore()
    assert api("ana").get(f"/api/changesets/{cs_id}")["status"] == "submitted"
    assert fake_db.raw["changeset_changes"].count_documents({"csId": cs_id}) == 1


# ── Revisión independiente (doc 104 §8): regresiones ────────────────────────


def test_owner_sin_model_edit_recibe_un_mensaje_claro(api, world, fake_db):
    cs_id = _draft(api, world)["id"]
    fake_db.raw["users"].update_one({"_id": "ana"}, {"$set": {"role": "lector"}})
    st, err = api("ana").call("POST", f"/api/changesets/{cs_id}/transfer", {"to": "carla"})
    assert st == 403 and "role can no longer edit models" in str(err)
    st, err = api("ana").call("DELETE", f"/api/changesets/{cs_id}")
    assert st == 403 and "role can no longer edit models" in str(err)


def test_no_se_elimina_un_draft_cuyo_publish_fallo_a_medias(api, world, monkeypatch, fake_db):
    """Un approve que falló a mitad del apply deja producción PARCIALMENTE
    escrita con las imágenes previas estampadas: el ledger es el único rastro
    (historial, rollback, reintento). Eliminar ese draft lo borraría."""
    ana = api("ana")
    cs_id = _draft(api, world)["id"]
    ana.change(cs_id, "canonical_columns", "c-tr-1", column_payload("t-transfer", "COD", "Codigo", 0, pk=True))
    real_apply = repository.apply_changes

    async def partial(plan, progress=None):
        await real_apply([p for p in plan if p[0] == plan[0][0]], progress=progress)   # llega la primera colección…
        raise TimeoutError("timeout a mitad del apply")                                  # …y el resto no

    monkeypatch.setattr(repository, "apply_changes", partial)
    ana.post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]})
    try:
        asyncio.run(service.review(cs_id, "beto", "approve", None))
    except TimeoutError:
        pass
    monkeypatch.setattr(repository, "apply_changes", real_apply)
    assert fake_db.raw["canonical_tables"].count_documents({"_id": "t-transfer", "flgactive": True}) == 1
    ana.post(f"/api/changesets/{cs_id}/withdraw")
    st, err = ana.call("DELETE", f"/api/changesets/{cs_id}")
    assert st == 409 and "may already be in production" in str(err)
    assert fake_db.raw["changeset_changes"].count_documents({"csId": cs_id}) == 2
    # Ronda 2: la marca es de la CABECERA — re-editar el draft no la borra
    # (re-editar reemplaza el documento del cambio y pierde su imagen previa).
    ana.change(cs_id, "canonical_tables", "t-transfer", table_payload("M_TRANSFER", "Transfer editada"))
    st, _ = ana.call("DELETE", f"/api/changesets/{cs_id}")
    assert st == 409
    assert _transfer(api, "ana", cs_id, {"to": "carla"})["owner"] == "carla"   # transferir sí (el ledger queda)


def test_un_approve_que_fallo_antes_de_escribir_en_produccion_no_bloquea_eliminar(api, world, monkeypatch, fake_db):
    """Ronda 2: estampar las imágenes previas ocurre ANTES de escribir en
    producción; si falla ahí, producción no se tocó y el draft se elimina."""
    ana = api("ana")
    cs_id = _draft(api, world)["id"]

    real_store = repository.store_before_images

    async def stamping_fails(cs, befores, keep_existing=True):
        first = dict(list(befores.items())[:1])
        await real_store(cs, first, keep_existing=keep_existing)     # estampa UNA entidad…
        raise TimeoutError("timeout estampando imágenes previas")    # …y se corta

    monkeypatch.setattr(repository, "store_before_images", stamping_fails)
    ana.post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]})
    try:
        asyncio.run(service.review(cs_id, "beto", "approve", None))
    except TimeoutError:
        pass
    assert fake_db.raw["canonical_tables"].count_documents({"_id": "t-transfer"}) == 0      # producción intacta
    assert fake_db.raw["changeset_changes"].count_documents({"csId": cs_id, "beforeAt": {"$exists": True}}) == 1
    ana.post(f"/api/changesets/{cs_id}/withdraw")
    assert ana.delete(f"/api/changesets/{cs_id}")["deleted"] is True


def test_carrera_borrar_una_tabla_del_canvas_no_queda_a_medias(api, world, monkeypatch, fake_db):
    """Ronda 2: borrar una tabla también la saca de los canvases (cascada).
    Canvases podados y borrado van en UNA escritura condicionada al dueño: si la
    versión cambia de manos justo antes, no se escribe NADA (antes quedaba el
    canvas podado sin el borrado de la tabla)."""
    cs_id = _draft(api, world)["id"]
    entry = service.transfer_entry("ana", "carla", "admin", None)
    real_bulk = repository.set_changes_bulk

    async def transfer_then_write(cs, items, owner=None):
        await repository.transfer_owner(cs, "ana", "carla", entry)    # llega justo antes de escribir
        return await real_bulk(cs, items, owner)

    monkeypatch.setattr(repository, "set_changes_bulk", transfer_then_write)
    st, _ = api("ana").change(cs_id, "canonical_tables", world["t2"], None, op="delete", expect=None)
    monkeypatch.setattr(repository, "set_changes_bulk", real_bulk)
    assert st == 403
    left = {(d["collection"], d["entityId"]) for d in fake_db.raw["changeset_changes"].find({"csId": cs_id})}
    assert left == {("canonical_tables", "t-transfer")}


def test_expected_owner_vacio_se_ignora(api, world):
    cs_id = _draft(api, world)["id"]
    assert _transfer(api, "admin", cs_id, {"to": "admin", "expectedOwner": ""})["owner"] == "admin"
    assert api("admin").delete(f"/api/changesets/{cs_id}?expectedOwner=")["deleted"] is True


def _transfer_in_between(monkeypatch, frm: str = "ana", to: str = "carla"):
    entry = service.transfer_entry(frm, to, "admin", None)
    return _stale_get(monkeypatch, lambda cid: repository.transfer_owner(cid, frm, to, entry))


def test_carrera_el_dueno_anterior_envia_despues_de_la_transferencia(api, world, monkeypatch, fake_db):
    cs_id = _draft(api, world)["id"]
    restore = _transfer_in_between(monkeypatch)
    api("ana").post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]}, expect=403)
    restore()
    doc = fake_db.raw["changesets"].find_one({"_id": cs_id})
    assert doc["owner"] == "carla" and doc["status"] == "draft" and not doc.get("requests")


def test_carrera_un_guardado_en_vuelo_del_dueno_anterior(api, world, monkeypatch, fake_db):
    cs_id = _draft(api, world)["id"]
    restore = _transfer_in_between(monkeypatch)
    api("ana").change(cs_id, "canonical_tables", "t-late", table_payload("M_LATE", "Late"), expect=403)
    restore()
    assert fake_db.raw["changeset_changes"].count_documents({"csId": cs_id, "entityId": "t-late"}) == 0


def test_carrera_un_lote_en_vuelo_del_dueno_anterior(api, world, monkeypatch, fake_db):
    cs_id = _draft(api, world)["id"]
    restore = _transfer_in_between(monkeypatch)
    api("ana").bulk(cs_id, [{"collection": "canonical_tables", "entityId": "t-late", "op": "upsert",
                             "payload": table_payload("M_LATE", "Late")}], expect=403)
    restore()
    assert fake_db.raw["changeset_changes"].count_documents({"csId": cs_id, "entityId": "t-late"}) == 0


def test_carrera_el_dueno_anterior_retira_el_request_del_nuevo(api, world, monkeypatch, fake_db):
    """Ana leyó SU request en revisión; en el medio lo rechazan (vuelve a draft),
    un admin lo transfiere a Carla y Carla lo re-envía: el withdraw tardío de Ana
    no debe retirar el request de Carla (el estado se repite: ABA)."""
    ana = api("ana")
    cs_id = _draft(api, world)["id"]
    ana.post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]})
    entry = service.transfer_entry("ana", "carla", "admin", None)

    async def concurrent(cid):
        await repository.transition(cid, "submitted", {"status": "draft", "submittedAt": None})
        await repository.transfer_owner(cid, "ana", "carla", entry)
        await repository.transition(cid, "draft", {"status": "submitted", "submittedAt": "2099-01-01T00:00:00+00:00"})

    restore = _stale_get(monkeypatch, concurrent)
    ana.post(f"/api/changesets/{cs_id}/withdraw", expect=403)
    restore()
    doc = fake_db.raw["changesets"].find_one({"_id": cs_id})
    assert doc["owner"] == "carla" and doc["status"] == "submitted"


def _delete_during(monkeypatch, method: str, cs_id: str):
    """Elimina la versión justo cuando `changeset_changes.<method>` va a
    escribir: el guardado ya pasó su paso 1 (touch) y leyó el cambio previo."""
    orig = getattr(FakeCollection, method)
    fired = {"n": 0}

    async def racing(self, *args, **kwargs):
        if self.name == "changeset_changes" and not fired["n"]:
            fired["n"] += 1
            assert await repository.delete_changeset(cs_id, "ana", service.DELETABLE) is not None
        return await orig(self, *args, **kwargs)

    monkeypatch.setattr(FakeCollection, method, racing)
    return lambda: monkeypatch.setattr(FakeCollection, method, orig)


def test_borrado_que_se_cruza_con_un_guardado_no_deja_huerfanos(api, world, monkeypatch, fake_db):
    cs_id = _draft(api, world)["id"]                                   # t-transfer ya en el ledger
    restore = _delete_during(monkeypatch, "replace_one", cs_id)
    assert asyncio.run(repository.set_change(cs_id, "canonical_tables", "t-transfer", "upsert",
                                             table_payload("M_TRANSFER2", "Transfer 2"))) is None
    restore()
    assert fake_db.raw["changesets"].count_documents({"_id": cs_id}) == 0
    assert fake_db.raw["changeset_changes"].count_documents({"csId": cs_id}) == 0


def test_borrado_que_se_cruza_con_un_lote_no_deja_huerfanos(api, world, monkeypatch, fake_db):
    ana = api("ana")
    cs_id = _draft(api, world)["id"]
    items = [{"collection": "canonical_tables", "entityId": f"t-b{i}", "op": "upsert",
              "payload": table_payload(f"M_B{i}", f"B{i}")} for i in range(5)]
    ana.bulk(cs_id, items)
    restore = _delete_during(monkeypatch, "bulk_write", cs_id)
    again = [{**it, "payload": table_payload(f"M_C{i}", f"C{i}")} for i, it in enumerate(items)]
    assert asyncio.run(repository.set_changes_bulk(cs_id, again)) is None
    restore()
    assert fake_db.raw["changeset_changes"].count_documents({"csId": cs_id}) == 0


def test_comentar_o_consultar_impacto_de_un_draft_eliminado_da_404(api, world):
    ana = api("ana")
    cs_id = _draft(api, world)["id"]
    ana.delete(f"/api/changesets/{cs_id}")
    ana.post(f"/api/changesets/{cs_id}/comments", {"text": "hola"}, expect=404)
    ana.get(f"/api/relationships/impact?columnId={world['c_a']}&changesetId={cs_id}", expect=404)
    # Sin versión o sobre producción, el impacto sigue respondiendo.
    assert ana.get(f"/api/relationships/impact?columnId={world['c_a']}")["total"] == 1


def test_el_dueno_que_vio_el_usuario_debe_seguir_siendolo(api, world):
    """Revisión de UI: el admin ve «draft de Ana»; en el medio Ana se lo pasa a
    Carla. Con `expectedOwner` (el dueño que mostró la pantalla), el admin no
    borra ni transfiere el draft de Carla creyendo que es de Ana."""
    cs_id = _draft(api, world)["id"]
    _transfer(api, "ana", cs_id, {"to": "carla"})
    _transfer(api, "admin", cs_id, {"to": "admin", "expectedOwner": "ana"}, expect=409)
    api("admin").delete(f"/api/changesets/{cs_id}?expectedOwner=ana", expect=409)
    assert api("carla").get(f"/api/changesets/{cs_id}")["owner"] == "carla"
    # con el dueño vigente, procede
    assert api("admin").delete(f"/api/changesets/{cs_id}?expectedOwner=carla")["deleted"] is True


def test_el_detalle_trae_cuantos_cambios_tiene_el_draft(api, world):
    """Lo que la confirmación de borrado promete es lo que se borra: TODOS los
    documentos de cambio (también lo creado y borrado dentro del mismo draft)."""
    ana = api("ana")
    cs_id = _draft(api, world)["id"]
    ana.change(cs_id, "canonical_tables", "t-tmp", table_payload("M_TMP", "Tmp"))
    ana.change(cs_id, "canonical_tables", "t-tmp", None, op="delete")      # creada y borrada en el draft
    detail = ana.get(f"/api/changesets/{cs_id}")
    assert detail["changeCount"] == 2
    assert ana.delete(f"/api/changesets/{cs_id}")["changes"] == 2



# ── Revisión independiente, ronda 3 ─────────────────────────────────────────


def test_un_apply_que_fallo_leyendo_antes_de_escribir_no_bloquea_eliminar(api, world, monkeypatch, fake_db):
    """El apply LEE antes de escribir (entidades borradas a revivir): si falla
    ahí, producción no se tocó y la versión debe poder eliminarse."""
    ana = api("ana")
    cs_id = _draft(api, world)["id"]
    real_find = FakeCollection.find

    def find_fails_on_revive_lookup(self, filter=None, projection=None):
        if (filter or {}).get("flgactive") is False and projection == {"flgactive": 1}:
            raise TimeoutError("timeout leyendo antes de escribir")
        return real_find(self, filter, projection)

    monkeypatch.setattr(FakeCollection, "find", find_fails_on_revive_lookup)
    ana.post(f"/api/changesets/{cs_id}/submit", {"reviewers": ["beto"]})
    try:
        asyncio.run(service.review(cs_id, "beto", "approve", None))
    except TimeoutError:
        pass
    monkeypatch.setattr(FakeCollection, "find", real_find)
    assert fake_db.raw["canonical_tables"].count_documents({"_id": "t-transfer"}) == 0      # producción intacta
    assert not fake_db.raw["changesets"].find_one({"_id": cs_id}).get("partialApplyAt")
    ana.post(f"/api/changesets/{cs_id}/withdraw")
    assert ana.delete(f"/api/changesets/{cs_id}")["deleted"] is True


def _transfer_during(monkeypatch, method: str, cs_id: str):
    """Transfiere la versión justo cuando `changeset_changes.<method>` va a
    escribir: el guardado del dueño anterior ya pasó su touch."""
    orig = getattr(FakeCollection, method)
    entry = service.transfer_entry("ana", "carla", "admin", None)
    fired = {"n": 0}

    async def racing(self, *args, **kwargs):
        if self.name == "changeset_changes" and not fired["n"]:
            fired["n"] += 1
            assert await repository.transfer_owner(cs_id, "ana", "carla", entry) is not None
        return await orig(self, *args, **kwargs)

    monkeypatch.setattr(FakeCollection, method, racing)
    return lambda: monkeypatch.setattr(FakeCollection, method, orig)


def test_un_guardado_que_se_cruza_con_la_transferencia_no_cae_en_el_draft_nuevo(api, world, monkeypatch, fake_db):
    cs_id = _draft(api, world)["id"]
    restore = _transfer_during(monkeypatch, "replace_one", cs_id)
    api("ana").change(cs_id, "canonical_tables", "t-late", table_payload("M_LATE", "Late"), expect=403)
    restore()
    assert fake_db.raw["changesets"].find_one({"_id": cs_id})["owner"] == "carla"
    assert fake_db.raw["changeset_changes"].count_documents({"csId": cs_id, "entityId": "t-late"}) == 0


def test_un_borrado_en_cascada_que_se_cruza_con_la_transferencia_no_cae_en_el_draft_nuevo(api, world, monkeypatch, fake_db):
    cs_id = _draft(api, world)["id"]
    restore = _transfer_during(monkeypatch, "bulk_write", cs_id)
    api("ana").change(cs_id, "canonical_tables", world["t2"], None, op="delete", expect=403)
    restore()
    left = {(d["collection"], d["entityId"]) for d in fake_db.raw["changeset_changes"].find({"csId": cs_id})}
    assert left == {("canonical_tables", "t-transfer")}


def test_carrera_la_marca_de_publish_a_medias_aparece_antes_de_eliminar(api, world, monkeypatch, fake_db):
    """El borrado va condicionado también a «sin publish a medias» en la misma
    sentencia (no sólo a lo que se leyó antes)."""
    cs_id = _draft(api, world)["id"]

    async def flag(cid):
        fake_db.raw["changesets"].update_one({"_id": cid}, {"$set": {"partialApplyAt": "2026-09-30T00:00:00+00:00"}})

    restore = _stale_get(monkeypatch, flag)
    api("ana").delete(f"/api/changesets/{cs_id}", expect=409)
    restore()
    assert fake_db.raw["changesets"].count_documents({"_id": cs_id}) == 1
