"""Doc 84 D1/A1 por HTTP: un approve que choca por nombre contra producción
responde 409 con `detail` ESTRUCTURADO {code, message, items, next} (lista
completa, en inglés, con la referencia de producción), el request vuelve a
`submitted` y producción queda intacta. Mismo guard y mismo momento que
siempre (doc 82: cada paso pasa por router/service/repositorio reales)."""
from __future__ import annotations

from tests.integration.conftest import build_world, table_payload


def test_approve_bloqueado_por_duplicados_devuelve_detalle_estructurado(api):
    w = build_world(api, name="Mundo Dup", prefix="d")
    pid = w["pid"]
    carla = api("carla")
    # Carla arma su draft ANTES de mirar producción y crea M_CLIENTE (ya publicada
    # en v2 por ana) con id nuevo — el caso de dos cargas paralelas (2026-09-10).
    cs = carla.post("/api/changesets/snapshot", {"projectId": pid}, expect=201)
    cs_id = cs["id"]
    # Distinto casing para que add_change no lo frene antes de tiempo — no: la
    # unicidad de add_change es case-insensitive contra publicado, así que el
    # choque se simula como carrera: la tabla entra con OTRO nombre y se
    # renombra... imposible por API. Camino real: publicar OTRA versión que
    # crea el nombre entre el add_change de carla y su approve.
    carla.change(cs_id, "canonical_tables", "d-tbl-nueva", table_payload("M_PRODUCTO", "Producto"))
    carla.change(cs_id, "canonical_columns", "d-col-nueva",
                 {"tableId": "d-tbl-nueva", "physicalName": "CODPRODUCTO", "logicalName": "Codigo",
                  "dataType": "BIGINT", "ordinal": 0})
    carla.post(f"/api/changesets/{cs_id}/submit", {"title": "carla", "reviewers": ["beto"]})

    # ana publica M_PRODUCTO primero (otra versión gana el nombre).
    ana = api("ana")
    cs_ana = ana.post("/api/changesets/snapshot", {"projectId": pid}, expect=201)["id"]
    ana.change(cs_ana, "canonical_tables", "d-tbl-ana", table_payload("m_producto", "Producto de ana"))
    ana.post(f"/api/changesets/{cs_ana}/submit", {"title": "ana", "reviewers": ["beto"]})
    beto = api("beto")
    assert beto.post(f"/api/changesets/{cs_ana}/review", {"decision": "approve"})["status"] == "approved"

    # El approve de carla se BLOQUEA con detalle estructurado.
    body = beto.post(f"/api/changesets/{cs_id}/review", {"decision": "approve"}, expect=409)
    detail = body["detail"]
    assert detail["code"] == "duplicate_names"
    assert detail["message"].startswith("Publish blocked: 1 table name already exists in production")
    assert "withdraw" in detail["next"]
    assert len(detail["items"]) == 1
    it = detail["items"][0]
    assert it["kind"] == "table" and it["name"] == "M_PRODUCTO" and it["where"] == "production"
    assert it["entityId"] == "d-tbl-nueva"
    assert it["existing"]["id"] == "d-tbl-ana" and it["existing"]["name"] == "m_producto"
    assert it["existing"]["owner"] == "ana" and it["existing"]["version"]
    assert it["existing"]["publishedAt"]
    # El request sigue en revisión y producción NO tiene la tabla de carla.
    assert beto.get(f"/api/changesets/{cs_id}")["status"] == "submitted"
    names = {t["physicalName"] for t in ana.get(f"/api/projects/{pid}/catalog/tables")}
    assert "m_producto" in names and "M_PRODUCTO" not in names
