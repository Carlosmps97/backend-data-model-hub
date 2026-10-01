"""Doc 105 (revisión R2, hallazgo 1) — el naming se valida en `standards/apply`.

Desde D1b el PUT `/settings/naming` (que validaba `case` y `scope`) responde
409 y el apply es el ÚNICO camino de escritura del naming. El apply no
validaba: un `case` desconocido, un scope desconocido o un `maxLength`
negativo se grababan (`maxLength` 0 es válido: límite desactivado). Con físicos derivables respondía 500 DESPUÉS de escribir
`naming_config` (sin versión); sin ellos respondía 200 y versionaba, y desde
ahí `/glossary/physicalize` y toda alta de columna en un draft daban 500
(`physicalize`/`apply_case` → ValueError). Ahora: 422 antes de escribir nada.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.main import app


def _apply_raw(pid, body, user="admin"):
    raw = TestClient(app, raise_server_exceptions=False)
    return raw.post(f"/api/projects/{pid}/standards/apply", json=body, headers={"X-Dev-User": user})


def _stored_naming(fake_db) -> list[dict]:
    return sorted(fake_db.raw["naming_config"].find({}), key=lambda d: d["_id"])


def _state(admin, pid, fake_db) -> tuple:
    """Lo que un apply rechazado NO debe tocar: la config grabada (cruda), la que
    lee la plataforma y el historial de versiones."""
    return (_stored_naming(fake_db), admin.get(f"/api/projects/{pid}/settings/naming"),
            len(admin.get(f"/api/projects/{pid}/standards/versions")))


INVALID = [
    pytest.param({"column": {"separator": "", "case": "snake", "maxLength": 150}}, id="case-desconocido-column"),
    pytest.param({"table": {"separator": "", "case": "snake", "maxLength": 150}}, id="case-desconocido-table"),
    pytest.param({"column": {"separator": "", "case": "UPPER", "maxLength": 150}}, id="case-en-mayusculas"),
    pytest.param({"table": {"separator": "", "case": "upper", "maxLength": -5}}, id="maxLength-negativo"),
    pytest.param({"vista": {"separator": "_", "case": "upper", "maxLength": 10}}, id="scope-desconocido"),
    # Un scope válido junto a uno inválido: tampoco se graba el válido.
    pytest.param({"column": {"separator": "_", "case": "lower", "maxLength": 60},
                  "table": {"separator": "", "case": "snake", "maxLength": 150}}, id="lote-mixto"),
]


@pytest.mark.parametrize("naming", INVALID)
def test_naming_invalido_es_422_y_no_escribe_nada_con_fisicos_derivables(api, world, fake_db, naming):
    """Con un físico DERIVABLE (sin override) el re-derivado reventaba DESPUÉS
    de grabar `naming_config`: 500, config inválida grabada y sin versión."""
    admin, pid = api("admin"), world["pid"]
    fake_db.raw["canonical_tables"].insert_one({"_id": "t-deriv", "projectId": pid, "flgactive": True,
                                               "logicalName": "Cliente", "physicalName": "CLIENTE"})
    before = _state(admin, pid, fake_db)
    r = _apply_raw(pid, {"kind": "naming", "namingConfig": naming})
    assert r.status_code == 422, (r.status_code, r.text[:300])
    assert _state(admin, pid, fake_db) == before
    assert fake_db.raw["canonical_tables"].find_one({"_id": "t-deriv"})["physicalName"] == "CLIENTE"


@pytest.mark.parametrize("naming", INVALID)
def test_naming_invalido_en_proyecto_sin_tablas_es_422_y_el_physicalize_sigue_vivo(api, fake_db, naming):
    """Proyecto sin tablas: el re-derivado no tenía nada que recorrer, el apply
    respondía 200 con el naming inválido grabado y versionado; desde ahí cada
    `physicalize` del proyecto daba 500."""
    admin = api("admin")
    pid = admin.post("/api/projects", {"name": "Vacio"}, expect=201)["id"]
    before = _state(admin, pid, fake_db)
    r = _apply_raw(pid, {"kind": "naming", "namingConfig": naming})
    assert r.status_code == 422, (r.status_code, r.text[:300])
    assert _state(admin, pid, fake_db) == before
    ph = TestClient(app, raise_server_exceptions=False).post(
        f"/api/projects/{pid}/glossary/physicalize", json={"logical": "Codigo Cliente", "scope": "column"},
        headers={"X-Dev-User": "admin"})
    assert ph.status_code == 200, ph.text[:200]


def test_naming_invalido_rechazado_no_traba_el_guardado_de_columnas(api, world):
    """Antes: tras un apply con `case` inválido (200 en el mundo base: todos
    sus físicos son override), CADA alta de columna en un draft daba 500."""
    admin, pid = api("admin"), world["pid"]
    r = _apply_raw(pid, {"kind": "naming", "namingConfig": {"column": {
        "separator": "", "case": "snake", "maxLength": 150}}})
    assert r.status_code == 422, (r.status_code, r.text[:300])
    cs = api("ana").post("/api/changesets/snapshot", {"projectId": pid}, expect=201)["id"]
    raw = TestClient(app, raise_server_exceptions=False)
    put = raw.put(f"/api/changesets/{cs}/changes", json={
        "collection": "canonical_columns", "entityId": "col-nueva", "op": "upsert",
        "payload": {"tableId": world["t1"], "physicalName": "NUEVA", "logicalName": "Nueva",
                    "dataType": "STRING", "ordinal": 5}}, headers={"X-Dev-User": "ana"})
    assert put.status_code == 200, put.text[:200]


def test_el_422_llega_antes_que_el_resto_del_lote(api, world, fake_db):
    """El naming inválido viaja con otros cambios válidos: no se escribe NADA
    del lote (ni el término, ni el dominio), ni se registra versión."""
    admin, pid = api("admin"), world["pid"]
    before = _state(admin, pid, fake_db)
    r = _apply_raw(pid, {"kind": "batch",
                         "termsUpsert": [{"term": "sucursal", "abbrev": "SUC", "scope": "column"}],
                         "domainsUpsert": [{"name": "Fecha", "defaultDataType": "DATE"}],
                         "namingConfig": {"table": {"separator": "", "case": "snake", "maxLength": 150}}})
    assert r.status_code == 422, (r.status_code, r.text[:300])
    assert _state(admin, pid, fake_db) == before
    assert admin.get(f"/api/projects/{pid}/glossary") == []
    assert admin.get(f"/api/projects/{pid}/domains") == []


@pytest.mark.parametrize("case", ["upper", "lower", "camel"])
def test_los_case_validos_siguen_pasando(api, world, case):
    """Control: los tres `case` del motor (`_VALID_CASES`) y `maxLength` ≥ 1
    siguen aplicándose y versionándose."""
    admin, pid = api("admin"), world["pid"]
    n0 = len(admin.get(f"/api/projects/{pid}/standards/versions"))
    v = admin.post(f"/api/projects/{pid}/standards/apply", {"kind": "naming", "namingConfig": {
        "column": {"separator": "", "case": case, "maxLength": 1}}})
    assert v["seq"] and len(admin.get(f"/api/projects/{pid}/standards/versions")) == n0 + 1
    col = admin.get(f"/api/projects/{pid}/settings/naming")["column"]
    assert {k: col[k] for k in ("separator", "case", "maxLength")} == {"separator": "", "case": case, "maxLength": 1}


def test_maxlength_0_es_limite_desactivado_y_se_respeta(api, world, fake_db):
    """Ronda 3: `maxLength` 0 = límite DESACTIVADO (así lo leen el guard de
    longitud del changeset y la carga Excel: `if not max_len`). La ronda 2 lo
    rechazaba (422) y la lectura lo cambiaba a 150: un proyecto sin límite
    pasaba a tener 150 sin que nadie lo pidiera."""
    admin, pid = api("admin"), world["pid"]
    admin.post(f"/api/projects/{pid}/standards/apply", {"kind": "naming", "namingConfig": {
        "column": {"separator": "", "case": "upper", "maxLength": 0}}})
    assert admin.get(f"/api/projects/{pid}/settings/naming")["column"]["maxLength"] == 0
    cs = api("ana").post("/api/changesets/snapshot", {"projectId": pid}, expect=201)["id"]
    api("ana").change(cs, "canonical_columns", "col-larga", {
        "tableId": world["t1"], "physicalName": "C" * 400, "logicalName": "Larga", "dataType": "STRING", "ordinal": 9})


def test_maxlength_booleano_no_se_acepta_como_numero(api, world, fake_db):
    admin, pid = api("admin"), world["pid"]
    before = _state(admin, pid, fake_db)
    r = _apply_raw(pid, {"kind": "naming", "namingConfig": {"column": {
        "separator": "", "case": "upper", "maxLength": True}}})
    assert r.status_code == 422, (r.status_code, r.text[:300])
    assert _state(admin, pid, fake_db) == before


def test_regrabar_el_valor_visible_limpia_uno_invalido_guardado(api, world, fake_db):
    """La lectura muestra el default en lugar de un valor inválido guardado
    (por API, antes de la validación); regrabar ESE valor visible tiene que
    escribirlo (antes: 422 «no changes» y el inválido quedaba para siempre,
    con un warning en cada lectura)."""
    from app.core.scope import naming_id

    admin, pid = api("admin"), world["pid"]
    fake_db.raw["naming_config"].update_one(
        {"_id": naming_id(pid, "column")},
        {"$set": {"projectId": pid, "scope": "column", "separator": "", "case": "snake", "maxLength": 150}},
        upsert=True)
    assert admin.get(f"/api/projects/{pid}/settings/naming")["column"]["case"] == "upper"   # lo visible
    admin.post(f"/api/projects/{pid}/standards/apply", {"kind": "naming", "namingConfig": {
        "column": {"separator": "", "case": "upper", "maxLength": 150}}})
    assert fake_db.raw["naming_config"].find_one({"_id": naming_id(pid, "column")})["case"] == "upper"
