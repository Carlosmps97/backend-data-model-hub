"""Doc 102: rutas del Reporting — lotes de ids en el CUERPO (fin del 431) y
`changesetId` = versión propia."""
from __future__ import annotations

from .helpers import P

ANA = {"X-Dev-User": "ana"}


def test_rutas_nuevas_registradas(client):
    paths = {r.path for r in client.app.routes}
    assert {"/api/reporting/columns/query", "/api/reporting/views/query"} <= paths


def test_columnas_por_post_sin_version(report_version_db, client):
    res = client.post("/api/reporting/columns/query", json={"projectId": P, "tableIds": ["t1", "t1", ""]})
    assert res.status_code == 200
    assert sorted(c["id"] for c in res.json()["data"]) == ["c1", "c2"]


def test_columnas_y_vistas_por_post_con_la_version(report_version_db, client):
    body = {"projectId": P, "tableIds": ["t1", "t4"], "changesetId": "cs1"}
    cols = client.post("/api/reporting/columns/query", json=body, headers=ANA)
    assert cols.status_code == 200 and sorted(c["id"] for c in cols.json()["data"]) == ["c1", "c4"]
    views = client.post("/api/reporting/views/query", json=body, headers=ANA)
    assert views.status_code == 200 and [v["id"] for v in views.json()["data"]] == ["v1", "v2"]


def test_lote_vacio_o_sobre_el_tope_es_422(report_version_db, client):
    assert client.post("/api/reporting/columns/query", json={"projectId": P, "tableIds": []}).status_code == 422
    many = [f"t{i}" for i in range(5001)]
    assert client.post("/api/reporting/views/query", json={"projectId": P, "tableIds": many}).status_code == 422


def test_get_con_version_de_otro_proyecto_es_409(report_version_db, client):
    res = client.get("/api/reporting/tables", params={"projectId": "p2", "changesetId": "cs1"}, headers=ANA)
    assert res.status_code == 409 and "another project" in res.json()["detail"]


def test_get_tablas_y_conteo_con_la_version(report_version_db, client):
    rows = client.get("/api/reporting/tables", params={"projectId": P, "changesetId": "cs1"}, headers=ANA).json()["data"]
    assert [r["physicalName"] for r in rows] == ["CLIENTE", "CUENTA_NUEVA", "NUEVA", "NUEVA_DOS"]
    count = client.get("/api/reporting/tables/count", params={"projectId": P, "changesetId": "cs1"}, headers=ANA)
    assert count.json()["data"] == {"total": 4}


def test_lote_con_solo_ids_vacios_es_422_y_no_todo_el_proyecto(report_version_db, client):
    """Revisión del doc 102 (A1): un lote que queda vacío al quitar los ids en
    blanco no puede caer al camino «sin lote» (todo el proyecto con tope)."""
    for path in ("/api/reporting/columns/query", "/api/reporting/views/query"):
        res = client.post(path, json={"projectId": P, "tableIds": ["", ""]})
        assert res.status_code == 422, (path, res.status_code, res.text[:200])
        assert res.json()["detail"] == "At least one table id is required."
