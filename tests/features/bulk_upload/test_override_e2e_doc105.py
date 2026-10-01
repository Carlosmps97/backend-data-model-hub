"""Doc 105 (revisión R2, hallazgo 4) — de punta a punta: el override de una
tabla/columna cuyo físico HOY coincide con el derivado sobrevive a una carga
Excel con un cambio real (descripción) y a la re-derivación siguiente del
glosario.

Recorre el camino real: contexto por el loader de la carga (con SUS
proyecciones: la BD de los tests, como Lakebase, devuelve sólo los campos
pedidos), `build_plan`, el lote al changeset (que estampa el override) y el
publish. El escenario: el modelador fija en la app el físico IGUAL al derivado
de una tabla/columna con override (el editor manda el flag; el estampado lo
conserva) y después sube un Excel que sólo cambia la descripción."""
from __future__ import annotations

import asyncio

import pytest

from app.features.bulk_upload import loader
from app.features.bulk_upload.planner import build_plan, referenced_table_ids
from tests.features.bulk_upload.helpers import crow, parsed, trow
from tests.integration.conftest import api, fake_db, publish, world  # noqa: F401  (fixtures)


def _apply_standards(api, pid: str, body: dict) -> None:
    api("admin").post(f"/api/projects/{pid}/standards/apply", body)


def _pin_physical_to_derived(api, world, coll: str, eid: str, physical: str) -> None:
    """El modelador renombra en la app al físico DERIVADO conservando el
    override (el editor manda el doc con el flag) y publica."""
    import copy
    u = api("ana")
    cs = u.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    doc = copy.deepcopy(u.get(f"/api/changesets/{cs}/effective/{coll}?ids={eid}"))
    assert doc, doc
    payload = {k: v for k, v in (doc[0] if isinstance(doc, list) else doc).items() if k != "id"}
    payload.update(physicalName=physical, physicalNameOverridden=True)
    u.change(cs, coll, eid, payload)
    publish(api, cs)


def _plan(api, world, rows_tables, rows_columns):
    """Valida el «Excel» como la carga (`service.validate_workbook`): contexto
    REAL del loader, columnas de las tablas referidas y `build_plan`."""
    cs = api("ana").post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    wb = parsed(tables=rows_tables, columns=rows_columns)

    async def _ctx():
        ctx = await loader.load_context(cs)
        ctx.columns_by_table = await loader.load_columns(cs, referenced_table_ids(wb, ctx))
        return ctx

    return cs, build_plan(wb, asyncio.run(_ctx()))


def _upload(api, world, rows_tables, rows_columns) -> str:
    """Valida y graba el plan en un draft (lo que hace la carga al aplicar).
    Devuelve el changeset."""
    cs, plan = _plan(api, world, rows_tables, rows_columns)
    changes = [{k: c[k] for k in ("collection", "entityId", "op", "payload")}
               for c in plan.changes if c["collection"] in ("canonical_tables", "canonical_columns")]
    assert changes, plan.report
    api("ana").bulk(cs, changes)
    return cs


def _published(fake_db, coll: str, eid: str) -> dict:
    return fake_db.raw[coll].find_one({"_id": eid})


def test_r2_columna_conserva_el_override_tras_una_carga_con_cambio_real(api, world, fake_db):
    pid = world["pid"]
    _pin_physical_to_derived(api, world, "canonical_columns", world["c_a"], "CODIGOCLIENTE")
    col = _published(fake_db, "canonical_columns", world["c_a"])
    assert (col["physicalName"], col["physicalNameOverridden"]) == ("CODIGOCLIENTE", True)
    cs = _upload(api, world, None, [crow(3, "Cliente", "Codigo Cliente", physical="CODIGOCLIENTE",
                                         description="Codigo unico del cliente")])
    publish(api, cs)
    col = _published(fake_db, "canonical_columns", world["c_a"])
    assert col["description"] == "Codigo unico del cliente"
    assert col["physicalNameOverridden"] is True
    # La consecuencia visible: la próxima re-derivación (acá, el naming de
    # columnas pasa a minúsculas) NO la renombra — sin el override quedaba
    # «codigocliente».
    _apply_standards(api, pid, {"kind": "naming", "namingConfig": {"column": {
        "separator": "", "case": "lower", "maxLength": 150}}})
    assert _published(fake_db, "canonical_columns", world["c_a"])["physicalName"] == "CODIGOCLIENTE"


# Tablas: el loader trae el pool PROYECTADO (`loader.TABLE_PROJECTION`). Antes del
# arreglo, sin `projectId` la validación fallaba (el job quedaba `failed`) en
# cuanto el Excel traía una tabla YA publicada, y sin los booleanos el update
# pisaba el override y las facetas con False.


def test_r2_recarga_identica_de_tabla_existente_con_el_loader_real(api, world, fake_db):
    _cs, plan = _plan(api, world, [trow(3, "Cliente", physical="M_CLIENTE", schema="STG",
                                        description="Maestro de clientes")], None)
    assert plan.report["summary"]["tables"] == {"create": 0, "update": 0, "unchanged": 1}


def test_r2_tabla_conserva_el_override_tras_una_carga_con_cambio_real(api, world, fake_db):
    _pin_physical_to_derived(api, world, "canonical_tables", world["t1"], "CLIENTE")
    t = _published(fake_db, "canonical_tables", world["t1"])
    assert (t["physicalName"], t["physicalNameOverridden"]) == ("CLIENTE", True)
    cs = _upload(api, world, [trow(3, "Cliente", physical="CLIENTE", schema="STG",
                                   description="Maestro de clientes (v2)")], None)
    publish(api, cs)
    t = _published(fake_db, "canonical_tables", world["t1"])
    assert t["description"] == "Maestro de clientes (v2)"
    assert t["physicalNameOverridden"] is True


def test_r2_tabla_conserva_sus_facetas_tras_una_carga_con_cambio_real(api, world, fake_db):
    fake_db.raw["canonical_tables"].update_one({"_id": world["t2"]}, {"$set": {"logicalOnly": True}})
    cs = _upload(api, world, [trow(3, "Cuenta", physical="M_CUENTA", schema="STG", description="Cuentas")], None)
    publish(api, cs)
    t = _published(fake_db, "canonical_tables", world["t2"])
    assert t["description"] == "Cuentas" and t["logicalOnly"] is True


def test_la_proyeccion_de_tablas_del_loader_trae_todos_los_campos_del_modelo():
    """Guarda (doc 105): el loader proyecta las tablas (a escala no baja docs
    completos). Todo campo de `CanonicalTableDoc` que falte en la proyección
    llega con su default al armar el update — `projectId` rompía la validación
    y los booleanos (override, facetas) se pisaban con False."""
    from app.features.catalog.models import CanonicalTableDoc

    fields = {(f.alias or name) for name, f in CanonicalTableDoc.model_fields.items()} - {"id"}
    assert fields - set(loader.TABLE_PROJECTION) == set()


def test_columna_que_solo_difiere_en_mayusculas_con_el_loader_real_y_el_changeset(api, world, fake_db):
    """Doc 105 (`apply_case` en el planner): la hoja trae «codcliente» para la
    columna CODCLIENTE — el changeset la grabaría igual. Sin otro cambio queda
    «unchanged»; con un cambio real (descripción) el update no la renombra y lo
    publicado es exactamente lo planeado (físico y override intactos)."""
    row = dict(table_logical="Cliente", logical="Codigo Cliente", physical="codcliente", pk=True)   # es la PK
    _cs, plan = _plan(api, world, None, [crow(3, **row)])
    assert plan.report["summary"]["columns"] == {"create": 0, "update": 0, "unchanged": 1}
    cs, plan = _plan(api, world, None, [crow(3, **row, description="Codigo del cliente")])
    assert [w["code"] for w in plan.report["warnings"]] == ["existing-column"]
    (ch,) = [c for c in plan.changes if c["collection"] == "canonical_columns"]
    api("ana").bulk(cs, [{k: ch[k] for k in ("collection", "entityId", "op", "payload")}])
    publish(api, cs)
    col = _published(fake_db, "canonical_columns", world["c_a"])
    assert (col["physicalName"], col["description"], col["physicalNameOverridden"]) == \
        (ch["payload"]["physicalName"], "Codigo del cliente", True) == ("CODCLIENTE", "Codigo del cliente", True)
