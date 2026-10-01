"""Doc 105 (U1): el directorio de usuarios sirve para DOS cosas — ofrecer
personas (revisores, destinos de transferencia: sólo activos) y resolver
nombres (quien dejó la empresa sigue siendo el autor de su versión). Con
`includeDisabled=true` vuelven también los deshabilitados, marcados."""
from __future__ import annotations


def _disable(fake_db, username: str) -> None:
    fake_db.raw["users"].update_one({"_id": username}, {"$set": {"status": "disabled"}})


def test_por_defecto_solo_activos(api, fake_db):
    _disable(fake_db, "carla")
    ids = {u["id"] for u in api("ana").get("/api/users")}
    assert "carla" not in ids and "ana" in ids


def test_para_nombres_incluye_deshabilitados_marcados(api, fake_db):
    _disable(fake_db, "carla")
    users = {u["id"]: u for u in api("ana").get("/api/users?includeDisabled=true")}
    assert users["carla"]["name"] == "Carla" and users["carla"]["disabled"] is True
    assert users["ana"].get("disabled") is not True


def test_ofrecer_personas_nunca_incluye_deshabilitados(api, fake_db):
    _disable(fake_db, "carla")
    ids = {u["id"] for u in api("ana").get("/api/users?can=model.edit&includeDisabled=true")}
    assert "carla" not in ids and "ana" in ids


def test_no_se_asigna_como_revisor_a_un_deshabilitado(api, fake_db, world):
    """Doc 105: el front no los ofrece, pero la API los aceptaba — y un
    revisor deshabilitado nunca vota: el request quedaba trabado."""
    _disable(fake_db, "beto")
    cs = api("ana").post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)
    status, body = api("ana").call("POST", f"/api/changesets/{cs['id']}/submit",
                                   {"title": "t", "reviewers": ["beto"]})
    assert status == 400 and "disabled" in body["detail"].lower() and "Beto" in body["detail"]
    assert api("ana").get(f"/api/changesets/{cs['id']}")["status"] == "draft"
