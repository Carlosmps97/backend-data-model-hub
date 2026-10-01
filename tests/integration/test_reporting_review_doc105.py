"""Doc 105 — revisión de los arreglos del Reporting, por HTTP sobre la app REAL
(routers, RBAC, versiones, ledger con sus `_id` deterministas)."""
from __future__ import annotations

from app.features.changesets import repository as cs_repo

REL_LOT = "/api/reporting/insights/relationships/query"


def _draft(api, w: dict, user: str = "ana") -> str:
    return api(user).post("/api/changesets/snapshot", {"projectId": w["pid"]}, expect=201)["id"]


# ── Hallazgo 1: un lote de relaciones no relee el ledger completo ─────────────
def test_h1_los_lotes_de_relaciones_no_leen_el_ledger_completo(api, world, fake_db, monkeypatch):
    """Base: repro del revisor R1 (`test_r1_rel_lots_ledger.py`). Cada lote leía
    con `changes_map` TODO el ledger de relaciones/tablas/columnas del draft."""
    ana, pid = api("ana"), world["pid"]
    cs = _draft(api, world)
    fake_db.raw["changeset_changes"].insert_many([
        {"_id": cs_repo.change_key(cs, "canonical_columns", f"cx-{i}"), "csId": cs,
         "collection": "canonical_columns", "entityId": f"cx-{i}", "op": "upsert", "at": "2026-09-30T00:00:00+00:00",
         "payload": {"projectId": pid, "tableId": f"t-otra-{i % 500}", "physicalName": f"C{i}",
                     "logicalName": f"C{i}", "dataType": "STRING", "ordinal": i}}
        for i in range(5000)])
    ana.change(cs, "canonical_columns", world["c_c"], {
        "tableId": world["t2"], "physicalName": "COD_CLIENTE_V2", "logicalName": "Codigo Cliente",
        "dataType": "STRING", "ordinal": 0})
    full_reads: list[int] = []
    real = cs_repo._ledger_map

    async def spy(cs_id, collections=None):
        out = await real(cs_id, collections)
        full_reads.append(sum(len(v) for v in out.values()))
        return out

    monkeypatch.setattr(cs_repo, "_ledger_map", spy)
    got = {}
    for lot in ([world["t1"]], [world["t2"]], ["t-otra-1"]):
        got[lot[0]] = [(r["parent"], r["child"])
                       for r in ana.post(REL_LOT, {"projectId": pid, "changesetId": cs, "tableIds": lot})]
    assert full_reads == []
    renamed = [("M_CLIENTE.CODCLIENTE", "M_CUENTA.COD_CLIENTE_V2")]      # el nombre de la VERSIÓN
    assert got == {world["t1"]: renamed, world["t2"]: renamed, "t-otra-1": []}


# ── Hallazgo 4: plantilla guardada antes del tope UTF-16 ─────────────────────
OLD_TEMPLATE = {"_id": "tpl-vieja", "flgactive": True, "name": "Vieja", "shared": True,
                "sheetName": "📊" * 20, "owner": "ana", "origin": "user",
                "columns": [{"header": "Tabla", "source": "table.physicalName"}]}


def test_h4_plantilla_guardada_antes_del_tope_utf16_se_lista(api, world, fake_db):
    """Repro del revisor R2 (`test_plantilla_guardada_antes_del_tope_utf16_rompe_el_listado`):
    el listado de TODO el proyecto era 500 (validaba el tope al LEER)."""
    fake_db.raw["sheet_templates"].insert_one({**OLD_TEMPLATE, "projectId": world["pid"]})
    listed = api("carla").get(f"/api/projects/{world['pid']}/sheet-templates")
    assert [(t["id"], t["sheetName"]) for t in listed] == [("tpl-vieja", "📊" * 20)]


def test_h4_editarla_exige_un_nombre_de_hoja_que_excel_acepte(api, world, fake_db):
    """Al ESCRIBIR el tope sigue: guardarla tal cual es 422; con un nombre válido, 200."""
    fake_db.raw["sheet_templates"].insert_one({**OLD_TEMPLATE, "projectId": world["pid"]})
    path = f"/api/projects/{world['pid']}/sheet-templates/tpl-vieja"
    body = {k: OLD_TEMPLATE[k] for k in ("name", "sheetName", "shared", "columns")}
    ana = api("ana")
    ana.put(path, body, expect=422)
    assert ana.put(path, {**body, "sheetName": "📊" * 15})["sheetName"] == "📊" * 15
