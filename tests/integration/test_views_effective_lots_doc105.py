"""Doc 105 (P2): «Import existing tables» con vistas pedía, en un draft, UN GET
por tabla (250 tablas = 250 lecturas de la cabecera y del ledger de vistas).
`GET /changesets/{cs}/effective/views?tableIds=a,b,…` devuelve en una lectura
lo MISMO que la unión de `?tableId=` por tabla — con el overlay del draft
(altas, bajas) y sin vistas de otras tablas. Sólo para vistas."""
from __future__ import annotations

from tests.integration.conftest import table_payload


def _ids(rows):
    return sorted(r["id"] for r in rows)


def test_lote_de_tablas_es_la_union_de_las_consultas_por_tabla(api, world):
    ana = api("ana")
    t1, t2 = world["t1"], world["t2"]
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    ana.change(cs, "canonical_tables", "t3", table_payload("M_TRES", "Tres"))
    ana.change(cs, "views", "v-new", {"name": "V_NUEVA", "schema": "STG_VU", "sourceTableIds": [t2],
                                      "sources": [{"column": "CODCLIENTE", "tableId": t2}]})
    ana.change(cs, "views", "v-other", {"name": "V_OTRA", "schema": "STG_VU", "sourceTableIds": ["t3"],
                                        "sources": [{"column": "X", "tableId": "t3"}]})
    ana.change(cs, "views", world["view"], None, op="delete")
    base = f"/api/changesets/{cs}/effective/views"
    union = _ids(ana.get(f"{base}?tableId={t1}")) + _ids(ana.get(f"{base}?tableId={t2}"))
    lot = ana.get(f"{base}?tableIds={t1},{t2}")
    assert _ids(lot) == sorted(set(union)) == ["v-new"]          # sin la borrada ni la de otra tabla
    assert _ids(ana.get(f"{base}?tableIds={t1},t3")) == ["v-other"]


def test_lote_en_produccion_y_validaciones(api, world):
    ana = api("ana")
    cs = ana.post("/api/changesets/snapshot", {"projectId": world["pid"]}, expect=201)["id"]
    assert _ids(ana.get(f"/api/changesets/{cs}/effective/views?tableIds={world['t1']}")) == [world["view"]]
    ana.get(f"/api/changesets/{cs}/effective/canonical_columns?tableIds={world['t1']}", expect=422)
    ana.get(f"/api/changesets/{cs}/effective/views?tableIds=,", expect=422)
    too_many = ",".join(f"t{i}" for i in range(301))
    ana.get(f"/api/changesets/{cs}/effective/views?tableIds={too_many}", expect=422)
