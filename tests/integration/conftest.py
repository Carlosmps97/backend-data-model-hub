"""Suite de INTEGRACIÓN en proceso (doc 82): la app FastAPI REAL — routers,
services, repositorios, guards de alcance, RBAC — sobre una BD en memoria con
semántica Mongo (`tests/support/fakedb.py`). Sólo se reemplaza el driver.

Por qué existe: los 500 de producción del 2026-09-09 (doc 80 §3/§8) eran
`TypeError`/`MissingProjectError` en líneas que NINGÚN test ejecutaba con las
firmas reales — la suite unitaria mockea los repositorios. Acá cada request
atraviesa el mismo código que en Apps; un error de cableado revienta aquí.

Fixtures:
- `fake_db`: BD vacía + roles de caja + usuarios por rol; queda instalada como
  singleton de `app.core.db.client` (sin lifespan: no toca Lakebase).
- `api`: fábrica de clientes por usuario (`api("ana")`) que desempaqueta el
  envelope `{success, data}` y falla con el body legible si el status no es el
  esperado.
- `world`: un proyecto con su v2 PUBLICADA (esquema, carpeta, canvas, dos
  tablas, columnas, relación, vista) — el escenario base de los flujos.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

from app.core.db import client as db_client
from app.main import app
from scripts.create_admin import build_roles
from tests.support.fakedb import FakeDb

USERS = {
    "admin": "administrador",
    "ana": "modelador",
    "carla": "modelador",
    "beto": "revisor",
    "diego": "lector",
}


@pytest.fixture
def fake_db(monkeypatch) -> FakeDb:
    db = FakeDb()
    now = datetime.now(timezone.utc).isoformat()
    for role in build_roles():
        db.raw["roles"].insert_one(role)
    for username, role in USERS.items():
        db.raw["users"].insert_one({
            "_id": username, "email": f"{username}@local", "name": username.title(), "role": role,
            "projectIds": [], "status": "active", "flgactive": True, "createdAt": now, "updatedAt": now,
        })
    monkeypatch.setattr(db_client, "_pg_db", db)
    monkeypatch.setattr(app.state, "db_connected", True, raising=False)
    return db


class Api:
    """Cliente HTTP de UN usuario (seam local `X-Dev-User`, sin REQUIRE_AUTH)."""

    def __init__(self, client: TestClient, user: str) -> None:
        self.client = client
        self.user = user

    def call(self, method: str, path: str, json=None, expect: int | None = None):
        r = self.client.request(method, path, json=json, headers={"X-Dev-User": self.user})
        try:
            body = r.json()
        except Exception:  # noqa: BLE001
            body = r.text
        data = body.get("data") if isinstance(body, dict) and "data" in body else body
        if expect is not None:
            assert r.status_code == expect, f"{self.user}: {method} {path} → {r.status_code}: {body}"
        return r.status_code, data

    def get(self, path, expect=200):
        return self.call("GET", path, expect=expect)[1]

    def post(self, path, json=None, expect=200):
        return self.call("POST", path, json=json, expect=expect)[1]

    def put(self, path, json=None, expect=200):
        return self.call("PUT", path, json=json, expect=expect)[1]

    def delete(self, path, expect=200):
        return self.call("DELETE", path, expect=expect)[1]

    # ── helpers de dominio ──────────────────────────────────────────────
    def change(self, cs_id: str, collection: str, entity_id: str, payload: dict | None,
               op: str = "upsert", expect: int = 200):
        return self.call("PUT", f"/api/changesets/{cs_id}/changes",
                         {"collection": collection, "entityId": entity_id, "op": op, "payload": payload},
                         expect=expect)

    def bulk(self, cs_id: str, changes: list[dict], expect: int = 200):
        return self.call("PUT", f"/api/changesets/{cs_id}/changes/bulk", {"changes": changes}, expect=expect)


@pytest.fixture
def api(fake_db):
    client = TestClient(app)
    return lambda user: Api(client, user)


# ── Escenario base ──────────────────────────────────────────────────────


def table_payload(physical: str, logical: str, schema: str = "STG", **extra) -> dict:
    return {"physicalName": physical, "logicalName": logical, "schema": schema, **extra}


def column_payload(table_id: str, physical: str, logical: str, ordinal: int, *, pk: bool = False,
                   data_type: str = "STRING", **extra) -> dict:
    p = {"tableId": table_id, "physicalName": physical, "logicalName": logical, "dataType": data_type,
         "ordinal": ordinal, "isNullable": not pk, **extra}
    if pk:
        p.update({"isPrimaryKey": True, "pkPosition": 0})
    return p


def publish(api, cs_id: str, owner: str = "ana", reviewer: str = "beto", title: str | None = None) -> dict:
    """submit (owner) + approve (reviewer) → cabecera aprobada con `appliedAt`."""
    api(owner).post(f"/api/changesets/{cs_id}/submit", {"title": title or cs_id, "reviewers": [reviewer]})
    out = api(reviewer).post(f"/api/changesets/{cs_id}/review", {"decision": "approve"})
    assert out["status"] == "approved" and out["appliedAt"], out
    return out


def build_world(api, name: str = "Modelo Test", owner: str = "ana", prefix: str = "w") -> dict:
    """Proyecto con v1 (marcador) + v2 publicada: esquema STG, carpeta, canvas,
    M_CLIENTE (PK CODCLIENTE + NBRCLIENTE), M_CUENTA (FK CODCLIENTE), relación
    M_CLIENTE→M_CUENTA y vista V_CLIENTE. Ids deterministas (con `prefix`, para
    que dos mundos convivan en la misma BD sin chocar) para las aserciones."""
    admin = api("admin")
    project = admin.post("/api/projects", {"name": name}, expect=201)
    pid = project["id"]
    versions = api(owner).get(f"/api/projects/{pid}/versions")
    v1 = next(v for v in versions if v["versionLabel"] == "v1")
    cs = api(owner).post("/api/changesets/snapshot", {"projectId": pid}, expect=201)
    cs_id = cs["id"]
    w = {"pid": pid, "v1": v1["id"], "cs2": cs_id,
         "schema": f"{prefix}-sch-stg", "folder": f"{prefix}-fld-1", "canvas": f"{prefix}-sa-1",
         "t1": f"{prefix}-tbl-cliente", "t2": f"{prefix}-tbl-cuenta",
         "c_a": f"{prefix}-col-cliente-cod", "c_b": f"{prefix}-col-cliente-nbr", "c_c": f"{prefix}-col-cuenta-cod",
         "rel": f"{prefix}-rel-1", "view": f"{prefix}-view-1"}
    u = api(owner)
    u.change(cs_id, "schemas", w["schema"], {"name": "STG", "kind": "tables"})
    u.change(cs_id, "folders", w["folder"], {"projectId": pid, "name": "Dominio Clientes"})
    u.change(cs_id, "canonical_tables", w["t1"], table_payload("M_CLIENTE", "Cliente", description="Maestro de clientes"))
    u.change(cs_id, "canonical_columns", w["c_a"], column_payload(w["t1"], "CODCLIENTE", "Codigo Cliente", 0, pk=True))
    u.change(cs_id, "canonical_columns", w["c_b"], column_payload(w["t1"], "NBRCLIENTE", "Nombre Cliente", 1))
    u.bulk(cs_id, [
        {"collection": "canonical_tables", "entityId": w["t2"], "op": "upsert",
         "payload": table_payload("M_CUENTA", "Cuenta")},
        {"collection": "canonical_columns", "entityId": w["c_c"], "op": "upsert",
         "payload": column_payload(w["t2"], "CODCLIENTE", "Codigo Cliente", 0, isForeignKey=True)},
    ])
    u.change(cs_id, "relationships", w["rel"], {
        "parentTableId": w["t1"], "childTableId": w["t2"],
        "pairs": [{"parentColumnId": w["c_a"], "childColumnId": w["c_c"]}]})
    u.change(cs_id, "views", w["view"], {
        "name": "V_CLIENTE", "schema": "STG_VU", "sourceTableIds": [w["t1"]],
        "sources": [{"column": "CODCLIENTE", "tableId": w["t1"]}], "showOnCanvas": True})
    u.change(cs_id, "subject_areas", w["canvas"], {
        "projectId": pid, "folderId": w["folder"], "name": "Modelo Clientes",
        "tableIds": [w["t1"], w["t2"]], "viewIds": [w["view"]],
        "layout": {w["t1"]: {"x": 0, "y": 0}, w["t2"]: {"x": 400, "y": 0}}})
    w["v2"] = publish(api, cs_id, owner=owner)
    return w


@pytest.fixture
def world(api) -> dict:
    return build_world(api)
