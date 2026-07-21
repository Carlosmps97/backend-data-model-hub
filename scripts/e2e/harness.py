"""Harness E2E — cliente HTTP contra el backend EN VIVO (localhost:8000).

Ejercita el stack completo: login real por rol → token JWT → header Authorization
→ `require_permission` (RBAC) → servicio → repositorio → Lakebase → auditoría.

- `Client(role)` loguea al usuario canónico del rol y guarda el token.
- Helpers de fixtures (`create_project`, `create_canvas`, `create_table`, …) hacen
  las mutaciones VÍA API y registran los ids creados para la limpieza.
- `cleanup()` borra por id DIRECTO en Mongo (hay entidades sin endpoint DELETE,
  p.ej. canonical_tables/columns) — así cada escenario no deja basura.
- `Suite` colecciona checks (name, ok, detail) y los vuelca como JSON.

Todo lo creado lleva el tag único `TAG` (por proceso) para poder barrer restos.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

BASE = os.getenv("E2E_BASE", "http://localhost:8000")

# Usuario canónico + password por rol (seed). admin/admin; demo 123456789.
ROLE_USER = {
    "admin": ("admin", "admin"),
    "modelador": ("carla", "123456789"),
    "modelador2": ("juan.castillo", "123456789"),
    "revisor": ("beto", "123456789"),
    "revisor2": ("ana", "123456789"),
    "lector": ("diego.torres", "123456789"),
}

TAG = f"E2E_{uuid.uuid4().hex[:8]}"       # tag único de este proceso/escenario
_CREATED: dict[str, list[str]] = {}        # colección -> [ids] para limpiar


def _track(collection: str, _id: str) -> str:
    _CREATED.setdefault(collection, []).append(_id)
    return _id


class Resp:
    __slots__ = ("status", "data", "raw")

    def __init__(self, r: httpx.Response):
        self.status = r.status_code
        try:
            body = r.json()
        except Exception:
            body = None
        # envelope ok(data) → {"data": ...}; devolvemos el payload interno si está.
        self.data = body.get("data") if isinstance(body, dict) and "data" in body else body
        self.raw = body

    def __repr__(self):
        return f"<Resp {self.status} {str(self.data)[:80]}>"


class Client:
    """Sesión autenticada de un rol."""

    def __init__(self, role: str):
        self.role = role
        user, pwd = ROLE_USER[role]
        self.user = user
        self.http = httpx.Client(base_url=BASE, timeout=60.0)
        r = self.http.post("/api/auth/login", json={"username": user, "password": pwd})
        if r.status_code != 200:
            raise RuntimeError(f"login {role}/{user} falló: {r.status_code} {r.text[:200]}")
        body = r.json().get("data") or r.json()
        self.token = body["token"]
        self.me = body["user"]
        self.http.headers["Authorization"] = f"Bearer {self.token}"

    def get(self, path, **kw): return Resp(self.http.get(path, **kw))
    def post(self, path, json=None, **kw): return Resp(self.http.post(path, json=json, **kw))
    def put(self, path, json=None, **kw): return Resp(self.http.put(path, json=json, **kw))
    def delete(self, path, **kw): return Resp(self.http.delete(path, **kw))

    # ── Fixtures (vía API, registrando ids) ──────────────────────────────────
    def create_project(self, name=None):
        r = self.post("/api/projects", {"name": name or f"{TAG} project", "description": TAG})
        return _track("projects", r.data["id"]) if r.status in (200, 201) else _fail("create_project", r)

    def create_canvas(self, project_id, name=None, folder_id=None):
        r = self.post("/api/subject-areas", {"projectId": project_id, "name": name or f"{TAG} canvas",
                                             "folderId": folder_id})
        return _track("subject_areas", r.data["id"]) if r.status in (200, 201) else _fail("create_canvas", r)

    def create_table(self, logical=None, schema="e2e", physical=None):
        r = self.post("/api/catalog/tables", {"logicalName": logical or f"{TAG} tabla",
                                              "schema": schema, "physicalName": physical})
        return _track("canonical_tables", r.data["id"]) if r.status in (200, 201) else _fail("create_table", r)

    def add_column(self, table_id, logical, dtype="STRING", domain=None, pk=False, ordinal=0):
        r = self.post(f"/api/catalog/tables/{table_id}/columns",
                      {"logicalName": logical, "dataType": dtype, "parentDomainId": domain,
                       "isPrimaryKey": pk, "ordinal": ordinal})
        return _track("canonical_columns", r.data["id"]) if r.status in (200, 201) else _fail("add_column", r)

    def create_relationship(self, p_tid, p_cid, c_tid, c_cid):
        r = self.post("/api/relationships", {"parentTableId": p_tid, "childTableId": c_tid,
                                             "pairs": [{"parentColumnId": p_cid, "childColumnId": c_cid}]})
        return _track("relationships", r.data["id"]) if r.status in (200, 201) else _fail("create_relationship", r)

    def create_view(self, name, sql, table_id=None):
        r = self.post("/api/views", {"name": name, "sql": sql, "tableId": table_id, "schema": "e2e"})
        return _track("views", r.data["id"]) if r.status in (200, 201) else _fail("create_view", r)

    def create_domain(self, name, dtype):
        r = self.post("/api/domains", {"name": name, "defaultDataType": dtype})
        return _track("parent_domains", r.data["id"]) if r.status in (200, 201) else _fail("create_domain", r)

    def snapshot(self, project_ids, title=None):
        r = self.post("/api/changesets/snapshot", {"title": title or f"{TAG} draft", "projectIds": project_ids})
        return _track("changesets", r.data["id"]) if r.status in (200, 201) else _fail("snapshot", r)


def _fail(op, r: Resp):
    raise RuntimeError(f"fixture {op} falló: {r.status} {str(r.raw)[:200]}")


# ── Limpieza directa en Mongo (por ids registrados) ──────────────────────────
async def _cleanup_async():
    from app.core.db.client import connect, disconnect, get_db
    await connect()
    db = await get_db()
    for coll, ids in _CREATED.items():
        if ids:
            await db[coll].delete_many({"_id": {"$in": ids}})
    # columnas de tablas creadas (por si se crearon sin registrar id)
    tids = _CREATED.get("canonical_tables", [])
    if tids:
        await db["canonical_columns"].delete_many({"tableId": {"$in": tids}})
    # changeset_changes de changesets creados
    csids = _CREATED.get("changesets", [])
    if csids:
        await db["changeset_changes"].delete_many({"csId": {"$in": csids}})
    await disconnect()


def cleanup():
    import asyncio
    try:
        asyncio.run(_cleanup_async())
    except Exception as e:  # noqa: BLE001
        print(f"[cleanup] aviso: {e}", file=sys.stderr)


class Suite:
    """Colector de checks de un escenario."""

    def __init__(self, name: str):
        self.name = name
        self.checks: list[dict] = []
        self.t0 = time.monotonic()

    def check(self, name: str, ok: bool, detail: str = ""):
        self.checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
        mark = "✓" if ok else "✗"
        print(f"  {mark} {name}" + (f" — {detail}" if detail else ""), file=sys.stderr)
        return ok

    def eq(self, name, got, expected):
        return self.check(name, got == expected, f"got={got!r} exp={expected!r}")

    def summary(self) -> dict:
        passed = sum(1 for c in self.checks if c["ok"])
        return {
            "scenario": self.name, "tag": TAG,
            "passed": passed, "failed": len(self.checks) - passed, "total": len(self.checks),
            "elapsed_ms": round((time.monotonic() - self.t0) * 1000),
            "checks": self.checks,
        }

    def done(self):
        s = self.summary()
        print("\n===E2E_RESULT===")
        print(json.dumps(s))
        return s
