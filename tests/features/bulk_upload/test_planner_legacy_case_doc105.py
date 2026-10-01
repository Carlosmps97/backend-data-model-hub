"""Doc 105 — el físico LEGADO de una columna (grabado antes del doc 83, fuera
de la regla de `case`) y una fila de la carga Excel con OTRO cambio.

Ronda 4: el changeset (`_apply_naming_rules`) aplica la regla de case a lo que
se TIPEA o cambia; un legado que el cambio repite tal cual queda tal cual
(renombrarlo en silencio dejaba vistas colgando, cambiaba el override y podía
chocar con otra columna). El planner hace lo mismo: el payload lleva el nombre
grabado y no hay aviso de renombre; sólo lo tipeado distinto se normaliza, y si
al grabarse chocaría (sin mirar mayúsculas) con otra columna de la tabla, la
validación lo marca como duplicado — no el apply.

Camino real, de punta a punta: loader de la carga, `build_plan`, el lote al
changeset y el publish — lo planeado es lo que se graba."""
from __future__ import annotations

import asyncio

from app.features.bulk_upload import loader
from app.features.bulk_upload.planner import build_plan, referenced_table_ids
from tests.features.bulk_upload.helpers import crow, parsed, trow
from tests.integration.conftest import api, fake_db, publish, world  # noqa: F401  (fixtures)


def _plan(api, world, rows_columns, rows_tables=None, user="ana"):
    cs = api(user).post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    wb = parsed(tables=rows_tables, columns=rows_columns)

    async def _ctx():
        ctx = await loader.load_context(cs)
        ctx.columns_by_table = await loader.load_columns(cs, referenced_table_ids(wb, ctx))
        return ctx

    return cs, build_plan(wb, asyncio.run(_ctx()))


def _write(api, cs, plan, user="ana") -> None:
    api(user).bulk(cs, [{k: c[k] for k in ("collection", "entityId", "op", "payload")} for c in plan.changes])


def _stored(fake_db, cs, eid) -> dict:
    return fake_db.raw["changeset_changes"].find_one({"csId": cs, "entityId": eid})["payload"]


def _published(fake_db, coll, eid) -> dict:
    return fake_db.raw[coll].find_one({"_id": eid})


def _legacy(fake_db, eid, physical, **extra) -> None:
    """Columna grabada ANTES del doc 83 (fuera de la regla de case)."""
    fake_db.raw["canonical_columns"].update_one({"_id": eid}, {"$set": {"physicalName": physical, **extra}})


def _camel(api, world, max_len=150) -> None:
    api("admin").post(f"/api/projects/{world['pid']}/standards/apply", {"kind": "naming", "namingConfig": {
        "column": {"separator": "", "case": "camel", "maxLength": max_len}}})


def _col_change(plan) -> dict:
    (ch,) = [c for c in plan.changes if c["collection"] == "canonical_columns"]
    return ch


def _codes(plan) -> list[str]:
    return [w["code"] for w in plan.report["warnings"]]


def test_legado_tipeado_identico_con_otro_cambio_queda_tal_cual_hasta_publicar(api, world, fake_db):
    _legacy(fake_db, world["c_b"], "nbrcliente")                              # regla vigente: UPPER
    cs, plan = _plan(api, world, [crow(3, "Cliente", "Nombre Cliente", physical="nbrcliente",
                                       description="Nombre completo")])
    ch = _col_change(plan)
    assert ch["payload"]["physicalName"] == "nbrcliente" and _codes(plan) == ["existing-column"]
    _write(api, cs, plan)
    assert _stored(fake_db, cs, world["c_b"])["physicalName"] == "nbrcliente"
    publish(api, cs)
    col = _published(fake_db, "canonical_columns", world["c_b"])
    assert (col["physicalName"], col["description"]) == ("nbrcliente", "Nombre completo")


def test_legado_por_match_logico_con_otro_cambio_queda_tal_cual(api, world, fake_db):
    _legacy(fake_db, world["c_b"], "nbr_cli")                                 # no calza con el derivado
    cs, plan = _plan(api, world, [crow(3, "Cliente", "Nombre Cliente", description="Nombre completo")])
    ch = _col_change(plan)
    _write(api, cs, plan)
    assert ch["payload"]["physicalName"] == _stored(fake_db, cs, world["c_b"])["physicalName"] == "nbr_cli"
    assert "rename" not in _codes(plan)


def test_camel_legado_largo_con_otro_cambio_valida_y_se_graba_igual(api, world, fake_db):
    """«NBR_CLIENTE_XYZ_LARGO» (21 > 15) no se renombra: el tope no lo penaliza
    (heredado) y el apply acepta lo que la validación aceptó."""
    _camel(api, world, max_len=15)
    _legacy(fake_db, world["c_b"], "NBR_CLIENTE_XYZ_LARGO")
    cs, plan = _plan(api, world, [crow(3, "Cliente", "Nombre Cliente", physical="NBR_CLIENTE_XYZ_LARGO",
                                       description="Nombre completo")])
    assert plan.report["errors"] == [] and not plan.has_errors
    _write(api, cs, plan)                                                     # 200 (antes: NameTooLongError)
    assert _stored(fake_db, cs, world["c_b"])["physicalName"] == "NBR_CLIENTE_XYZ_LARGO"


def test_la_vista_vu_nueva_referencia_el_fisico_que_se_graba(api, world, fake_db):
    _camel(api, world)
    _legacy(fake_db, world["c_b"], "nbr_cliente")
    cs, plan = _plan(api, world, [crow(3, "Cliente", "Nombre Cliente", physical="nbr_cliente", description="Nombre")],
                     rows_tables=[trow(3, "Cliente", physical="M_CLIENTE", schema="STG")])
    (view,) = [c for c in plan.changes if c["collection"] == "views"]
    _write(api, cs, plan)
    stored_col = _stored(fake_db, cs, world["c_b"])["physicalName"]
    assert stored_col == "nbr_cliente"
    assert stored_col in {s["column"] for s in _stored(fake_db, cs, view["entityId"])["sources"]}


def test_las_vistas_existentes_siguen_apuntando_al_legado(api, world, fake_db):
    """Repro R8: la app re-apunta las vistas al RENOMBRAR (viewSync, en el
    front); la carga no. Con el legado intacto, V_CLIENTE sigue válida."""
    _camel(api, world)
    _legacy(fake_db, world["c_a"], "cod_cliente")
    fake_db.raw["views"].update_one({"_id": world["view"]},
                                    {"$set": {"sources": [{"column": "cod_cliente", "tableId": world["t1"]}]}})
    cs, plan = _plan(api, world, [crow(3, "Cliente", "Codigo Cliente", physical="cod_cliente", pk="X",
                                       description="Codigo unico")])
    assert not plan.has_errors, plan.report["errors"]
    _write(api, cs, plan)
    assert _stored(fake_db, cs, world["c_a"])["physicalName"] == "cod_cliente"


def test_legado_que_choca_con_otra_columna_repetido_tal_cual_se_graba(api, world, fake_db):
    """Repro R8: dos legadas «nbr_cliente» y «nbrcliente» en la misma tabla.
    Repetir «nbr_cliente» tal cual (con otra descripción) no la renombra: no
    choca, valida sin errores y el apply acepta."""
    _camel(api, world)
    _legacy(fake_db, world["c_b"], "nbr_cliente")
    other = dict(fake_db.raw["canonical_columns"].find_one({"_id": world["c_b"]}))
    other.update({"_id": "c-legacy-2", "physicalName": "nbrcliente", "logicalName": "Nombre Corto", "ordinal": 9})
    fake_db.raw["canonical_columns"].insert_one(other)
    cs, plan = _plan(api, world, [crow(3, "Cliente", "Nombre Cliente", physical="nbr_cliente",
                                       description="Nombre completo")])
    assert not plan.has_errors, plan.report["errors"]
    assert _col_change(plan)["payload"]["physicalName"] == "nbr_cliente"
    _write(api, cs, plan)                                                     # 200 (antes: 409 duplicado)


def test_tipeado_que_al_grabarse_choca_con_otra_columna_lo_marca_la_validacion(api, world, fake_db):
    """«NBR_CLIENTE» renombra la legada «nbr_cliente» a «nbrCliente», que choca
    con «nbrcliente»: la validación lo marca (el changeset daría 409)."""
    _camel(api, world)
    _legacy(fake_db, world["c_b"], "nbr_cliente")
    other = dict(fake_db.raw["canonical_columns"].find_one({"_id": world["c_b"]}))
    other.update({"_id": "c-legacy-2", "physicalName": "nbrcliente", "logicalName": "Nombre Corto", "ordinal": 9})
    fake_db.raw["canonical_columns"].insert_one(other)
    cs, plan = _plan(api, world, [crow(3, "Cliente", "Nombre Cliente", physical="NBR_CLIENTE")])
    assert [(e["code"], e["row"]) for e in plan.report["errors"]] == [("duplicate-name", 3)]
    # Lo que habría mandado la carga: el servidor lo rechaza igual.
    doc = {k: v for k, v in fake_db.raw["canonical_columns"].find_one({"_id": world["c_b"]}).items()
           if k not in ("_id", "flgactive", "createdAt", "updatedAt")}
    st, _body = api("ana").call("PUT", f"/api/changesets/{cs}/changes", {
        "collection": "canonical_columns", "entityId": world["c_b"], "op": "upsert",
        "payload": {**doc, "physicalName": "nbrCliente"}})
    assert st == 409


def test_excel_y_app_dejan_el_mismo_override_en_el_legado(api, world, fake_db):
    """Repro R8: el kit deja override=True en un legado cuyo físico sólo difiere
    del derivado en el case. La misma edición (sólo la descripción) por la app
    y por Excel graba el mismo físico (el legado) y el mismo flag."""
    _legacy(fake_db, world["c_b"], "nbrcliente", logicalName="Nbrcliente", physicalNameOverridden=True)
    cs_app = api("ana").post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    doc = {k: v for k, v in fake_db.raw["canonical_columns"].find_one({"_id": world["c_b"]}).items()
           if k not in ("_id", "flgactive", "createdAt", "updatedAt")}
    api("ana").change(cs_app, "canonical_columns", world["c_b"], {**doc, "description": "Nombre completo"})
    by_app = _stored(fake_db, cs_app, world["c_b"])
    cs_xl, plan = _plan(api, world, [crow(3, "Cliente", "Nbrcliente", physical="nbrcliente",
                                          description="Nombre completo")], user="carla")
    _write(api, cs_xl, plan, user="carla")
    by_excel = _stored(fake_db, cs_xl, world["c_b"])
    assert (by_app["physicalName"], by_app["physicalNameOverridden"]) == \
        (by_excel["physicalName"], by_excel["physicalNameOverridden"]) == ("nbrcliente", True)


def test_sin_fisico_el_legado_que_difiere_solo_en_case_queda_hasta_publicar(api, world, fake_db):
    """Repro R13 (ronda 5): sin CAMPO_FISICO, «nombrecliente» (el derivado de
    «Nombre Cliente» es NOMBRECLIENTE) se conserva, como la edición desde la app."""
    _legacy(fake_db, world["c_b"], "nombrecliente")
    cs, plan = _plan(api, world, [crow(3, "Cliente", "Nombre Cliente", description="Nombre completo")])
    ch = _col_change(plan)
    assert ch["payload"]["physicalName"] == "nombrecliente" and "rename" not in _codes(plan)
    _write(api, cs, plan)
    publish(api, cs)
    col = _published(fake_db, "canonical_columns", world["c_b"])
    assert (col["physicalName"], col["description"]) == ("nombrecliente", "Nombre completo")


def test_fila_que_trae_una_columna_con_borrado_pendiente_se_graba_como_se_planea(api, world, fake_db):
    """Alineación con la regla del servidor (ronda 5): la columna legada con un
    borrado PENDIENTE en el draft no está en el estado efectivo — la fila la
    planea NUEVA (otro id) con el nombre tipeado normalizado; para un id nuevo
    el servidor también normaliza (no hay pendiente ni publicado que conservar)
    y la unicidad no choca con la borrada. Lo planeado es lo grabado."""
    _legacy(fake_db, world["c_b"], "nbrcliente")
    cs = api("ana").post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    api("ana").change(cs, "canonical_columns", world["c_b"], None, op="delete")
    wb = parsed(columns=[crow(3, "Cliente", "Nombre Cliente", physical="nbrcliente", data_type="STRING")])

    async def _ctx():
        ctx = await loader.load_context(cs)
        ctx.columns_by_table = await loader.load_columns(cs, referenced_table_ids(wb, ctx))
        return ctx

    plan = build_plan(wb, asyncio.run(_ctx()))
    assert not plan.has_errors, plan.report["errors"]
    ch = _col_change(plan)
    assert ch["entityId"] != world["c_b"] and ch["payload"]["physicalName"] == "NBRCLIENTE"
    _write(api, cs, plan)
    assert _stored(fake_db, cs, ch["entityId"])["physicalName"] == "NBRCLIENTE"
