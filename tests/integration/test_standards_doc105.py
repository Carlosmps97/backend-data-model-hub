"""Doc 105 — Data Standards por HTTP: un apply sin cambios no registra versión
(P1); una edición idéntica de un término tampoco, ni re-deriva nada (P1-bis);
con un término bloqueado, el 409 del bloqueo sale antes que el 422 de campo
vacío (C1)."""
from __future__ import annotations

NO_CHANGES = "There are no changes to apply."


def _apply(admin, pid, body, expect=200):
    return admin.call("POST", f"/api/projects/{pid}/standards/apply", body, expect=expect)[1]


def test_p1_apply_sin_cambios_es_422_y_no_registra_version(api, world):
    admin, pid = api("admin"), world["pid"]
    n0 = len(admin.get(f"/api/projects/{pid}/standards/versions"))
    for body in ({}, {"kind": "ddl", "title": "x", "ddlConfigPatch": {}},
                 {"kind": "glossary", "termsUpsert": [], "termsDelete": [], "namingConfig": {}}):
        assert _apply(admin, pid, body, expect=422)["detail"] == NO_CHANGES
    assert len(admin.get(f"/api/projects/{pid}/standards/versions")) == n0


def test_p1bis_edicion_identica_de_un_termino_no_registra_version(api, world):
    admin, pid = api("admin"), world["pid"]
    _apply(admin, pid, {"kind": "glossary",
                        "termsUpsert": [{"term": " Sucursal ", "abbrev": " SUC ", "scope": "column"}]})
    (t,) = admin.get(f"/api/projects/{pid}/glossary")
    assert (t["term"], t["abbrev"]) == ("Sucursal", "SUC")          # se guarda recortado
    n0 = len(admin.get(f"/api/projects/{pid}/standards/versions"))
    for term, abbrev in (("Sucursal", "SUC"), ("  Sucursal", "SUC  ")):
        body = {"kind": "glossary", "termsUpsert": [{"id": t["id"], "term": term, "abbrev": abbrev,
                                                     "scope": "column"}]}
        assert _apply(admin, pid, body, expect=422)["detail"] == NO_CHANGES
    assert len(admin.get(f"/api/projects/{pid}/standards/versions")) == n0
    # Junto a un cambio real, la versión nombra SÓLO el cambio real.
    v = _apply(admin, pid, {"kind": "batch",
                            "termsUpsert": [{"id": t["id"], "term": "Sucursal ", "abbrev": "SUC", "scope": "column"}],
                            "domainsUpsert": [{"name": "Fecha", "defaultDataType": "DATE"}]})
    assert v["diff"] == {"added": ["Domain Fecha · DATE"], "edited": [], "removed": []}
    assert len(admin.get(f"/api/projects/{pid}/standards/versions")) == n0 + 1


def test_apply_de_un_proyecto_no_borra_estandares_de_otro(api, world):
    """Los repos de estándares borran por `_id`: una baja con el id de OTRO
    proyecto lo soft-deleteaba (incluso un término bloqueado — el guard del
    lock mira sólo el proyecto del apply). Doc 105: esas bajas se descartan."""
    from tests.integration.conftest import build_world
    admin, pa = api("admin"), world["pid"]
    pb = build_world(api, "Proyecto B", prefix="b")["pid"]
    _apply(admin, pb, {"kind": "batch", "termsUpsert": [{"term": "sucursal", "abbrev": "SUC", "scope": "column"}],
                       "domainsUpsert": [{"name": "Fecha", "defaultDataType": "DATE"}],
                       "udpUpsert": [{"name": "Ajena", "level": "table"}]})
    (term,) = admin.get(f"/api/projects/{pb}/glossary")
    (dom,) = admin.get(f"/api/projects/{pb}/domains")
    udp = next(u for u in admin.get(f"/api/projects/{pb}/udp") if u["name"] == "Ajena")
    admin.post(f"/api/projects/{pb}/glossary/{term['id']}/lock", {})
    n0 = len(admin.get(f"/api/projects/{pa}/standards/versions"))
    out = _apply(admin, pa, {"kind": "batch", "termsDelete": [term["id"]], "domainsDelete": [dom["id"]],
                             "udpDelete": [udp["id"]]}, expect=422)
    assert out["detail"] == NO_CHANGES
    assert len(admin.get(f"/api/projects/{pa}/standards/versions")) == n0
    assert [t["id"] for t in admin.get(f"/api/projects/{pb}/glossary")] == [term["id"]]
    assert [d["id"] for d in admin.get(f"/api/projects/{pb}/domains")] == [dom["id"]]
    assert any(u["id"] == udp["id"] for u in admin.get(f"/api/projects/{pb}/udp"))


def test_upsert_con_el_id_de_otro_proyecto_es_409_y_el_lote_no_deja_nada(api, world, fake_db):
    """Un upsert con el id de un estándar de OTRO proyecto caía al alta y
    chocaba por clave duplicada (500) DESPUÉS de las escrituras previas del
    lote (la baja de un término propio quedaba hecha). Doc 105: 409 antes de
    escribir nada."""
    from starlette.testclient import TestClient

    from app.main import app
    from tests.integration.conftest import build_world
    admin, pa = api("admin"), world["pid"]
    pb = build_world(api, "Proyecto B", prefix="b")["pid"]
    _apply(admin, pb, {"kind": "batch", "termsUpsert": [{"term": "sucursal", "abbrev": "SUC", "scope": "column"}],
                       "domainsUpsert": [{"name": "Fecha", "defaultDataType": "DATE"}],
                       "udpUpsert": [{"name": "Ajena", "level": "table"}]})
    (term_b,) = admin.get(f"/api/projects/{pb}/glossary")
    (dom_b,) = admin.get(f"/api/projects/{pb}/domains")
    udp_b = next(u for u in admin.get(f"/api/projects/{pb}/udp") if u["name"] == "Ajena")
    fake_db.raw["ddl_rules"].insert_one({"_id": "rule-b", "projectId": pb, "name": "regla_b", "flgactive": True})
    _apply(admin, pa, {"kind": "glossary", "termsUpsert": [{"term": "cuenta", "abbrev": "CTA", "scope": "column"}]})
    (own,) = admin.get(f"/api/projects/{pa}/glossary")
    n0 = len(admin.get(f"/api/projects/{pa}/standards/versions"))
    raw = TestClient(app, raise_server_exceptions=False)
    foreign = {"termsUpsert": [{"id": term_b["id"], "term": "otra", "abbrev": "OTR", "scope": "column"}],
               "domainsUpsert": [{"id": dom_b["id"], "name": "Otro", "defaultDataType": "DATE"}],
               "udpUpsert": [{"id": udp_b["id"], "name": "Otra", "level": "table"}],
               "rulesUpsert": [{"id": "rule-b", "name": "otra_regla"}]}
    for block, items in foreign.items():
        body = {"kind": "batch", "termsDelete": [own["id"]], "namingConfig": {"table": {
            "separator": "_", "case": "upper", "maxLength": 90}}, block: items}
        r = raw.post(f"/api/projects/{pa}/standards/apply", json=body, headers={"X-Dev-User": "admin"})
        assert r.status_code == 409 and r.json()["detail"].endswith("belongs to another project."), (block, r.text)
    assert [t["id"] for t in admin.get(f"/api/projects/{pa}/glossary")] == [own["id"]]      # la baja no se hizo
    assert admin.get(f"/api/projects/{pa}/settings/naming")["table"]["maxLength"] == 150   # ni el naming
    assert len(admin.get(f"/api/projects/{pa}/standards/versions")) == n0
    assert [t["term"] for t in admin.get(f"/api/projects/{pb}/glossary")] == ["sucursal"]
    assert [d["name"] for d in admin.get(f"/api/projects/{pb}/domains")] == ["Fecha"]


def test_upserts_que_se_grabarian_iguales_no_registran_version(api, world):
    """Dominios, UDP, reglas y config DDL: se compara el documento que el apply
    grabaría (normalizado), no el body — orden de claves, opcionales ausentes
    o en blanco, claves que la normalización descarta."""
    admin, pid = api("admin"), world["pid"]
    rule = {"name": "regla_x", "condition": "", "action": {}, "appliesTo": ["ddl.tabla_fisica"]}
    _apply(admin, pid, {"kind": "batch",
                        "domainsUpsert": [{"name": "Fecha", "defaultDataType": "DATE", "udpValues": {"a": "1", "b": "2"}}],
                        "udpUpsert": [{"name": "Clase", "level": "table"}],
                        "rulesUpsert": [rule],
                        "ddlConfigPatch": {"lookups": {"a": {"values": {"x": "1"}}, "b": {"values": {}}},
                                           "output": {"typeCase": "upper"}}})
    (dom,) = admin.get(f"/api/projects/{pid}/domains")
    udp = next(u for u in admin.get(f"/api/projects/{pid}/udp") if u["name"] == "Clase")
    (stored_rule,) = admin.get(f"/api/projects/{pid}/ddl-rules")
    n0 = len(admin.get(f"/api/projects/{pid}/standards/versions"))
    for body in (
        {"domainsUpsert": [{"id": dom["id"], "name": "Fecha", "defaultDataType": "date",
                            "udpValues": {"b": "2", "a": "1"}, "physicalName": "", "description": None}]},
        {"udpUpsert": [{"id": udp["id"], "name": "Clase", "level": "table", "allowedValues": ["no-es-lista"]}]},
        {"rulesUpsert": [{"id": stored_rule["id"], **rule}]},
        {"ddlConfigPatch": {"lookups": {"b": {"values": {}}, "a": {"values": {"x": "1"}}},
                            "output": {"typeCase": "upper", "clave_desconocida": 1}}},
    ):
        assert _apply(admin, pid, {"kind": "batch", **body}, expect=422)["detail"] == NO_CHANGES, body
    assert len(admin.get(f"/api/projects/{pid}/standards/versions")) == n0
    # Mixto: el dominio idéntico no cuenta; el UDP sí cambia.
    v = _apply(admin, pid, {"kind": "batch",
                            "domainsUpsert": [{"id": dom["id"], "name": "Fecha", "defaultDataType": "DATE"}],
                            "udpUpsert": [{"id": udp["id"], "name": "Clase", "level": "table", "description": "d"}]})
    assert v["diff"] == {"added": [], "edited": ["UDP Clase"], "removed": []}
    # Un espacio en el nombre SÍ es un cambio: el apply lo grabaría (el nombre no se recorta).
    _apply(admin, pid, {"kind": "domain", "domainsUpsert": [{"id": dom["id"], "name": "Fecha ",
                                                             "defaultDataType": "DATE"}]})


def test_naming_identico_no_registra_version(api, world):
    admin, pid = api("admin"), world["pid"]
    cur = admin.get(f"/api/projects/{pid}/settings/naming")["column"]
    n0 = len(admin.get(f"/api/projects/{pid}/standards/versions"))
    rule = {k: cur[k] for k in ("separator", "case", "maxLength")}
    assert _apply(admin, pid, {"kind": "naming", "namingConfig": {"column": rule}}, expect=422)["detail"] == NO_CHANGES
    assert len(admin.get(f"/api/projects/{pid}/standards/versions")) == n0
    v = _apply(admin, pid, {"kind": "naming", "namingConfig": {"column": {**rule, "maxLength": 60}}})
    assert v["diff"]["edited"] == [f"Column naming · sep '{rule['separator']}' · {rule['case']} · max 60"]


def test_c1_bloqueado_sale_409_aunque_venga_vacio(api, world):
    admin, pid = api("admin"), world["pid"]
    _apply(admin, pid, {"kind": "glossary", "termsUpsert": [{"term": "sucursal", "abbrev": "SUC", "scope": "column"}]})
    (t,) = admin.get(f"/api/projects/{pid}/glossary")
    admin.post(f"/api/projects/{pid}/glossary/{t['id']}/lock", {})
    for abbrev in ("", "   "):
        out = _apply(admin, pid, {"kind": "glossary", "termsUpsert": [
            {"id": t["id"], "term": "sucursal", "abbrev": abbrev, "scope": "column"}]}, expect=409)
        assert "locked" in out["detail"]
    (after,) = admin.get(f"/api/projects/{pid}/glossary")
    assert (after["abbrev"], after["locked"]) == ("SUC", True)
