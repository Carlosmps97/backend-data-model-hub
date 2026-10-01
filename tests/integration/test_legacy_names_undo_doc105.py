"""Doc 105 (ronda 5, revisor R13) — nombres legados y el Undo del front (doc 50).

La regla de la ronda 4 conserva un físico legado que el cambio repite tal cual
(no se renombra en silencio: las vistas lo referencian por nombre). Pero el
«vigente» excluía las columnas con un borrado PENDIENTE en el draft y, con un
renombre pendiente, sólo contaba ese nombre: el Undo del front — que re-graba
la columna con su pre-imagen, por id — la renombraba («nbrcliente» →
«NBRCLIENTE», o «nbrCliente» en camel, con la vista restaurada colgando).
Un nombre que coincide con el pendiente O con el publicado ya existe: no se
tipeó, se conserva.
"""
from __future__ import annotations


def _doc(fake_db, eid: str) -> dict:
    return {k: v for k, v in fake_db.raw["canonical_columns"].find_one({"_id": eid}).items()
            if k not in ("_id", "flgactive", "createdAt", "updatedAt")}


def _stored(fake_db, cs: str, eid: str) -> dict:
    return fake_db.raw["changeset_changes"].find_one({"csId": cs, "entityId": eid})


def test_undo_de_borrar_una_columna_legada_la_devuelve_tal_cual(api, world, fake_db):
    fake_db.raw["canonical_columns"].update_one({"_id": world["c_b"]}, {"$set": {"physicalName": "nbrcliente"}})
    pre = _doc(fake_db, world["c_b"])                                   # pre-imagen (colPre del front)
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs, "canonical_columns", world["c_b"], None, op="delete")
    # Undo «Delete column»: el mismo doc, por id, en un lote.
    ana.bulk(cs, [{"collection": "canonical_columns", "entityId": world["c_b"], "op": "upsert", "payload": pre}])
    stored = _stored(fake_db, cs, world["c_b"])["payload"]["physicalName"]
    # Esperado: vuelve tal cual (no hay renombre contra producción). Real: «NBRCLIENTE».
    assert stored == "nbrcliente", stored


def test_undo_de_borrar_en_camel_la_vista_restaurada_encuentra_su_columna(api, world, fake_db):
    api("admin").post(f"/api/projects/{world['pid']}/standards/apply", {"kind": "naming", "namingConfig": {
        "column": {"separator": "", "case": "camel", "maxLength": 150}}})
    fake_db.raw["canonical_columns"].update_one({"_id": world["c_b"]}, {"$set": {"physicalName": "nbr_cliente"}})
    fake_db.raw["views"].update_one({"_id": world["view"]}, {"$set": {"sources": [
        {"column": "CODCLIENTE", "tableId": world["t1"]}, {"column": "nbr_cliente", "tableId": world["t1"]}]}})
    col_pre = _doc(fake_db, world["c_b"])
    view_pre = {k: v for k, v in fake_db.raw["views"].find_one({"_id": world["view"]}).items()
                if k not in ("_id", "flgactive", "createdAt", "updatedAt")}
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    # Borrar la columna (el front también la saca de la vista).
    ana.change(cs, "canonical_columns", world["c_b"], None, op="delete")
    ana.change(cs, "views", world["view"], {**view_pre, "sources": view_pre["sources"][:1]})
    # Undo: columna + vista con sus pre-imágenes.
    ana.bulk(cs, [{"collection": "canonical_columns", "entityId": world["c_b"], "op": "upsert", "payload": col_pre},
                  {"collection": "views", "entityId": world["view"], "op": "upsert", "payload": view_pre}])
    col = _stored(fake_db, cs, world["c_b"])["payload"]["physicalName"]
    view_cols = {s["column"].lower() for s in _stored(fake_db, cs, world["view"])["payload"]["sources"]}
    # Esperado: la vista restaurada encuentra su columna. Real: columna «nbrCliente», vista «nbr_cliente».
    assert col.lower() in view_cols, (col, view_cols)


def test_undo_de_un_renombre_devuelve_el_nombre_publicado(api, world, fake_db):
    fake_db.raw["canonical_columns"].update_one({"_id": world["c_b"]}, {"$set": {"physicalName": "nbrcliente"}})
    pre = _doc(fake_db, world["c_b"])
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs, "canonical_columns", world["c_b"], {**pre, "physicalName": "NBR_CLI"})   # renombre
    ana.change(cs, "canonical_columns", world["c_b"], pre)                                  # Undo «Edit column»
    stored = _stored(fake_db, cs, world["c_b"])["payload"]["physicalName"]
    # Esperado: el nombre publicado («nbrcliente»). Real: «NBRCLIENTE» (renombre vs. producción).
    assert stored == "nbrcliente", stored


# Doc 105 (ronda 5, R15): el nombre legado que se conserva arrastraba el flag
# `physicalNameOverridden`: se recalculaba sobre el físico CONSERVADO (minúsculas)
# contra el derivado (mayúsculas) y una columna DERIVABLE quedaba «custom» con
# sólo editar su descripción o deshacer su borrado (diff espurio en la versión y
# el Glosario ya no la re-derivaba). Conservado el nombre, se conserva su flag.
def _legacy_derivable(fake_db, world) -> dict:
    fake_db.raw["canonical_columns"].update_one({"_id": world["c_b"]}, {"$set": {
        "physicalName": "nombre_cliente", "physicalNameOverridden": False}})
    return _doc(fake_db, world["c_b"])


def test_undo_de_borrar_una_columna_legada_conserva_su_flag_de_override(api, world, fake_db):
    pre = _legacy_derivable(fake_db, world)
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs, "canonical_columns", world["c_b"], None, op="delete")
    ana.bulk(cs, [{"collection": "canonical_columns", "entityId": world["c_b"], "op": "upsert", "payload": pre}])
    stored = _stored(fake_db, cs, world["c_b"])["payload"]
    assert stored["physicalName"] == "nombre_cliente"
    assert stored["physicalNameOverridden"] is False, stored


def test_editar_solo_la_descripcion_de_una_columna_legada_no_la_marca_custom(api, world, fake_db):
    pre = _legacy_derivable(fake_db, world)
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs, "canonical_columns", world["c_b"], {**pre, "description": "solo la descripción"})
    stored = _stored(fake_db, cs, world["c_b"])["payload"]
    assert (stored["physicalName"], stored["physicalNameOverridden"]) == ("nombre_cliente", False), stored


def test_sin_el_flag_en_el_payload_rige_el_del_nombre_existente(api, world, fake_db):
    fake_db.raw["canonical_columns"].update_one({"_id": world["c_b"]}, {"$set": {
        "physicalName": "nombre_cliente", "physicalNameOverridden": True}})
    pre = {k: v for k, v in _doc(fake_db, world["c_b"]).items() if k != "physicalNameOverridden"}
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs, "canonical_columns", world["c_b"], {**pre, "description": "otra"})
    stored = _stored(fake_db, cs, world["c_b"])["payload"]
    assert (stored["physicalName"], stored["physicalNameOverridden"]) == ("nombre_cliente", True), stored


def test_un_nombre_tipeado_se_normaliza_y_su_flag_se_estampa_como_siempre(api, world, fake_db):
    pre = _legacy_derivable(fake_db, world)
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs, "canonical_columns", world["c_b"], {**pre, "physicalName": "cod_nuevo"})
    stored = _stored(fake_db, cs, world["c_b"])["payload"]
    assert (stored["physicalName"], stored["physicalNameOverridden"]) == ("COD_NUEVO", True), stored


# Doc 105 (ronda 6, R16-N1): conservado el legado, su flag se conserva SÓLO si
# el lógico no cambió. Con un lógico nuevo (la hoja Excel con CAMPO_FISICO = el
# legado y otro CAMPO_LOGICO) el físico ya no es el derivado del lógico: queda
# «custom» (override True), como la misma edición sobre un físico no legado —
# si no, el próximo re-derivado del Glosario lo renombraba en silencio.
def test_conservar_el_legado_con_un_logico_nuevo_lo_marca_custom(api, world, fake_db):
    fake_db.raw["canonical_columns"].update_one({"_id": world["c_b"]}, {"$set": {
        "physicalName": "nombre_cliente", "logicalName": "Nombre Cliente", "physicalNameOverridden": False}})
    pre = _doc(fake_db, world["c_b"])
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs, "canonical_columns", world["c_b"], {**pre, "logicalName": "Razon Social"})
    stored = _stored(fake_db, cs, world["c_b"])["payload"]
    assert (stored["physicalName"], stored["physicalNameOverridden"]) == ("nombre_cliente", True), stored


def test_con_el_mismo_logico_el_flag_se_conserva_aunque_venga_en_otra_edicion(api, world, fake_db):
    fake_db.raw["canonical_columns"].update_one({"_id": world["c_b"]}, {"$set": {
        "physicalName": "nombre_cliente", "logicalName": "Nombre Cliente", "physicalNameOverridden": False}})
    pre = _doc(fake_db, world["c_b"])
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs, "canonical_columns", world["c_b"], {**pre, "logicalName": "Razon Social"})
    ana.change(cs, "canonical_columns", world["c_b"], {**pre, "description": "otra", "logicalName": "Nombre Cliente"})
    stored = _stored(fake_db, cs, world["c_b"])["payload"]
    assert (stored["physicalName"], stored["physicalNameOverridden"]) == ("nombre_cliente", False), stored


# Doc 105 (ronda 7, R18b): un lógico que sólo cambia mayúsculas («Nombre
# Cliente» → «NOMBRE CLIENTE») deriva el MISMO físico: es el mismo estado y el
# flag se conserva (el planner arma ese payload con una fila sin físico
# declarado; antes quedaba True: diff espurio y el Glosario dejaba de re-derivarla).
def test_un_logico_que_solo_cambia_mayusculas_conserva_el_flag(api, world, fake_db):
    fake_db.raw["canonical_columns"].update_one({"_id": world["c_b"]}, {"$set": {
        "physicalName": "nombre_cliente", "logicalName": "Nombre Cliente", "physicalNameOverridden": False}})
    pre = _doc(fake_db, world["c_b"])
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs, "canonical_columns", world["c_b"], {**pre, "logicalName": "NOMBRE CLIENTE"})
    stored = _stored(fake_db, cs, world["c_b"])["payload"]
    assert (stored["physicalName"], stored["physicalNameOverridden"]) == ("nombre_cliente", False), stored
