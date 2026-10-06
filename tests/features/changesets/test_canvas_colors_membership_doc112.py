"""Doc 112 — el color de una caja EN un canvas (`subject_areas.colors[id]`) vive
mientras la caja está en ese canvas. Borrar la tabla o la vista la saca de los
canvases (cascada del doc 88 §4) y con ella sale su color de cada canvas: antes
quedaba guardado y, si la caja volvía, revivía un color viejo. Lo mismo al
podar en el gate los miembros que el draft borra, y en la carga Excel: una
tabla que ENTRA a un canvas llega con el color de su tabla."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.bulk_upload.planner import build_plan
from app.features.changesets import service

from tests.features.bulk_upload.helpers import by_coll, crow, ctx, parsed, seq_ids, trow

SA_PUB = {"id": "sa1", "projectId": "p1", "name": "D", "tableIds": ["t1", "t2"], "viewIds": ["v1", "v2"],
          "layout": {"t1": {"x": 1, "y": 1}, "t2": {"x": 4, "y": 4}, "v1": {"x": 2, "y": 2}, "v2": {"x": 3, "y": 3}},
          "drawings": [], "udpValues": {},
          "colors": {"t1": "theme:principal", "t2": "none", "v1": "#92D050", "v2": "#FFC000"}}


def _cs(monkeypatch, pending: dict | None = None):
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"projectId": "p1", "id": "cs1", "owner": "ana", "status": "draft"}))
    monkeypatch.setattr(service.repository, "changes_map",
                        AsyncMock(side_effect=lambda cs_id, colls=None: {c: dict((pending or {}).get(c, {})) for c in (colls or pending or {})}))
    monkeypatch.setattr(service.settings_service, "get_naming_for", AsyncMock(return_value={"maxLength": 150}))
    monkeypatch.setattr(service.dict_svc, "naming_rules", AsyncMock(return_value=({}, "", "upper")))


def _published(canvases: list[dict]):
    async def _pub(collection, flt=None, **kw):
        if collection == "subject_areas":
            return [dict(c) for c in canvases]
        if collection == "canonical_tables":
            return [{"id": "t1", "projectId": "p1"}, {"id": "t2", "projectId": "p1"}]
        if collection == "views":
            return [{"id": "v1", "projectId": "p1"}, {"id": "v2", "projectId": "p1"}]
        return []
    return _pub


def test_poda_del_gate_quita_tambien_el_color_de_los_miembros_borrados():
    pending = {"views": {"v1": {"op": "delete"}}, "canonical_tables": {"t2": {"op": "delete"}}}
    out = service.prune_deleted_members(dict(SA_PUB), pending)
    assert out["colors"] == {"t1": "theme:principal", "v2": "#FFC000"}
    # un canvas sin colores no gana la llave
    assert "colors" not in service.prune_deleted_members({"tableIds": ["t1", "t2"]}, pending)


def test_borrar_una_vista_le_quita_su_color_en_cada_canvas(monkeypatch):
    other = {**SA_PUB, "id": "sa2", "name": "D2", "colors": {"v1": "none", "t1": "#00B050"}}
    _cs(monkeypatch, pending={"subject_areas": {}, "views": {}, "canonical_tables": {}, "folders": {}})
    monkeypatch.setattr(service.repository, "published", _published([SA_PUB, other]))
    bulk = AsyncMock(return_value={"id": "cs1"})
    monkeypatch.setattr(service.repository, "set_changes_bulk", bulk)

    asyncio.run(service.add_change("cs1", "ana", "views", "v1", "delete", None))

    canvases = {it["entityId"]: it["payload"] for it in bulk.await_args.args[1] if it["collection"] == "subject_areas"}
    assert canvases["sa1"]["colors"] == {"t1": "theme:principal", "t2": "none", "v2": "#FFC000"}
    assert canvases["sa2"]["colors"] == {"t1": "#00B050"}


def test_borrar_una_tabla_le_quita_su_color_en_cada_canvas(monkeypatch):
    _cs(monkeypatch, pending={"subject_areas": {}, "views": {}, "canonical_tables": {}, "folders": {}})
    monkeypatch.setattr(service.repository, "published", _published([SA_PUB]))
    bulk = AsyncMock(return_value={"id": "cs1"})
    monkeypatch.setattr(service.repository, "set_changes_bulk", bulk)

    asyncio.run(service.add_change("cs1", "ana", "canonical_tables", "t1", "delete", None))

    canvas = next(it["payload"] for it in bulk.await_args.args[1] if it["collection"] == "subject_areas")
    assert canvas["tableIds"] == ["t2"]
    assert canvas["colors"] == {"t2": "none", "v1": "#92D050", "v2": "#FFC000"}


def test_dos_borrados_del_lote_sobre_el_mismo_canvas_quitan_los_dos_colores(monkeypatch):
    _cs(monkeypatch, pending={"subject_areas": {}, "views": {}, "canonical_tables": {}, "folders": {}})
    monkeypatch.setattr(service.repository, "published", _published([SA_PUB]))
    bulk = AsyncMock(return_value={"id": "cs1"})
    monkeypatch.setattr(service.repository, "set_changes_bulk", bulk)

    asyncio.run(service.add_changes_bulk("cs1", "ana", [
        {"collection": "views", "entityId": "v1", "op": "delete", "payload": None},
        {"collection": "views", "entityId": "v2", "op": "delete", "payload": None},
    ]))

    canvas = next(it["payload"] for it in bulk.await_args.args[1] if it["collection"] == "subject_areas")
    assert canvas["viewIds"] == []
    assert canvas["colors"] == {"t1": "theme:principal", "t2": "none"}


def test_carga_excel_una_tabla_que_entra_al_canvas_llega_con_el_color_de_su_tabla():
    """Un color guardado para un id que NO está en el canvas (dato viejo: la
    tabla salió antes del doc 112) no revive cuando la carga la vuelve a meter;
    los colores de las cajas que ya estaban se conservan."""
    c = ctx(canvases=[{"id": "sa1", "projectId": "p1", "folderId": None, "name": "D", "tableIds": ["t0"],
                       "layout": {"t0": {"x": 40, "y": 1000}}, "drawings": [], "udpValues": {},
                       "colors": {"t0": "#92D050", "t9": "#FF0000"}}],
            tables=[{"id": "t0", "projectId": "p1", "physicalName": "VIEJA", "logicalName": "Vieja", "schema": "ddv"},
                    {"id": "t9", "projectId": "p1", "physicalName": "NUEVA", "logicalName": "Nueva", "schema": "ddv"}])
    p = parsed(tables=[trow(3, "Nueva", diagram="D", schema="ddv")],
               columns=[crow(3, "Nueva", "Codigo", data_type="STRING", pk=True)])
    canvas, = by_coll(build_plan(p, c, new_id=seq_ids()))["subject_areas"]
    assert "t9" in canvas["payload"]["tableIds"]
    assert canvas["payload"]["colors"] == {"t0": "#92D050"}
