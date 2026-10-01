"""Harness E2E — cliente HTTP contra el backend (vivo o en memoria).

Ejercita el stack completo: login real por rol → token JWT → header Authorization
→ `require_permission` (RBAC) → servicio → repositorio → BD → auditoría.

- `Client(role)` loguea al usuario canónico del rol y guarda el token.
- Cada escenario trabaja en un proyecto PROPIO (`create_project`, doc 75): las
  rutas del modelo y de los estándares van bajo `/api/projects/{pid}/…`.
- Doc 105: el MODELO se escribe SIEMPRE por una versión. `Seed` junta las
  fixtures (esquemas, carpetas, tablas, columnas, relaciones, vistas, canvases)
  y las publica en UN draft (snapshot → `/changes/bulk` → submit → review); las
  escrituras directas a producción responden 409. Los estándares van por
  `POST /api/projects/{pid}/standards/apply` (versionado, con rollback).
- `cleanup()` borra DIRECTO en la BD los proyectos registrados con todo lo suyo
  (colecciones de alcance, changesets y su ledger, jobs de la carga Excel y sus
  cuerpos) y los ids sueltos
  registrados con `_track` (usuarios, roles) — el backend vivo no queda con
  basura. En memoria no hace nada: la BD fake se descarta.
- `Suite` colecciona checks (name, ok, detail) y los vuelca como JSON.

Destino: `E2E_BASE` (default `http://localhost:8000`). `use_app(app)` corre los
escenarios contra la app EN MEMORIA (TestClient de Starlette); lo usa
`tests/scripts/test_e2e_inprocess.py` con la BD fake de los tests.

Todo lo creado lleva el tag único `TAG` (por proceso) para poder barrer restos.
"""
from __future__ import annotations

import json
import os
import re
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
# Matriz de rol (colección `roles`) de cada usuario canónico — para sembrarlos
# en una BD de prueba (en vivo ya existen).
ROLE_MATRIX = {"admin": "administrador", "modelador": "modelador", "modelador2": "modelador",
               "revisor": "revisor", "revisor2": "revisor", "lector": "lector"}

TAG = f"E2E_{uuid.uuid4().hex[:8]}"       # tag único de este proceso/escenario
_CREATED: dict[str, list[str]] = {}        # colección -> [ids] para limpiar
_HTTP_FACTORY = None                       # en memoria: fábrica de clientes de la app


def use_app(app) -> None:
    """Corre los escenarios contra `app` EN MEMORIA (TestClient). La BD la
    instala quien llama; `cleanup()` queda sin efecto."""
    global _HTTP_FACTORY
    from starlette.testclient import TestClient
    _HTTP_FACTORY = lambda: TestClient(app)   # noqa: E731


def _http() -> httpx.Client:
    return _HTTP_FACTORY() if _HTTP_FACTORY else httpx.Client(base_url=BASE, timeout=60.0)


def _track(collection: str, _id: str) -> str:
    _CREATED.setdefault(collection, []).append(_id)
    return _id


def _uid(kind: str) -> str:
    return f"{TAG}-{kind}-{uuid.uuid4().hex[:6]}"


def phys(logical: str) -> str:
    """Nombre físico legible derivado del lógico (`E2E_AB12_SALDO_ACTUAL`)."""
    return re.sub(r"[^0-9A-Za-z]+", "_", logical).strip("_").upper()


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

    @property
    def detail(self):
        """`detail` de un error (HTTPException) o None."""
        return self.raw.get("detail") if isinstance(self.raw, dict) else None

    def __repr__(self):
        return f"<Resp {self.status} {str(self.data)[:80]}>"


class Client:
    """Sesión autenticada de un rol."""

    def __init__(self, role: str):
        self.role = role
        user, pwd = ROLE_USER[role]
        self.user = user
        self.http = _http()
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
    def patch(self, path, json=None, **kw): return Resp(self.http.patch(path, json=json, **kw))
    def delete(self, path, **kw): return Resp(self.http.delete(path, **kw))

    # ── Proyecto (alta directa por diseño, doc 75 D5) ────────────────────────
    def create_project(self, name=None) -> str:
        r = self.post("/api/projects", {"name": name or f"{TAG} project {uuid.uuid4().hex[:4]}",
                                        "description": TAG})
        return _track("projects", r.data["id"]) if r.status in (200, 201) else _fail("create_project", r)

    # ── Versiones (el ÚNICO camino de escritura del modelo) ─────────────────
    def draft(self, project_id: str, title=None) -> str:
        r = self.post("/api/changesets/snapshot", {"projectId": project_id, "title": title or f"{TAG} draft"})
        return r.data["id"] if r.status in (200, 201) else _fail("snapshot", r)

    def change(self, cs_id, collection, entity_id, payload=None, op="upsert") -> Resp:
        return self.put(f"/api/changesets/{cs_id}/changes",
                        {"collection": collection, "entityId": entity_id, "op": op, "payload": payload})

    def bulk(self, cs_id, changes: list[dict]) -> Resp:
        return self.put(f"/api/changesets/{cs_id}/changes/bulk", {"changes": changes})

    def publish(self, cs_id, reviewer: "Client", title=None) -> Resp:
        """submit (dueño) + approve (revisor). Devuelve la respuesta del review."""
        r = self.post(f"/api/changesets/{cs_id}/submit", {"title": title or f"{TAG} request",
                                                          "reviewers": [reviewer.user]})
        if r.status != 200:
            return r
        return reviewer.post(f"/api/changesets/{cs_id}/review", {"decision": "approve"})

    # ── Estándares (versionados, doc 105 D1b) ───────────────────────────────
    def apply_standards(self, project_id, body: dict) -> Resp:
        return self.post(f"/api/projects/{project_id}/standards/apply", body)

    def domain_id(self, project_id, name) -> str | None:
        return next((d["id"] for d in (self.get(f"/api/projects/{project_id}/domains").data or [])
                     if d["name"] == name), None)

    def create_domain(self, project_id, name, dtype) -> str:
        r = self.apply_standards(project_id, {"kind": "domain",
                                              "domainsUpsert": [{"name": name, "defaultDataType": dtype}]})
        return self.domain_id(project_id, name) if r.status == 200 else _fail("create_domain", r)

    # ── Lecturas de producción del proyecto ─────────────────────────────────
    def tables(self, project_id, **params) -> list[dict]:
        return self.get(f"/api/projects/{project_id}/catalog/tables", params=params).data or []

    def table(self, project_id, table_id) -> dict | None:
        return next((t for t in self.tables(project_id) if t["id"] == table_id), None)

    def columns(self, table_id) -> list[dict]:
        return self.get(f"/api/catalog/tables/{table_id}/columns").data or []


class Seed:
    """Fixtures del MODELO por el camino versionado (doc 105): junta altas en
    memoria y `publish()` las escribe en UN draft del dueño y lo publica con el
    revisor. Ids con el TAG; el físico se deriva del lógico si no se da."""

    def __init__(self, owner: Client, project_id: str):
        self.owner, self.pid = owner, project_id
        self.changes: list[dict] = []
        self._schemas = {(s.get("name") or "").upper()
                         for s in (owner.get(f"/api/projects/{project_id}/schemas").data or [])}
        self._folder: str | None = None

    def _add(self, collection: str, payload: dict, entity_id: str | None = None) -> str:
        eid = entity_id or _uid(collection)
        self.changes.append({"collection": collection, "entityId": eid, "op": "upsert", "payload": payload})
        return eid

    def schema(self, name: str, kind: str = "tables") -> None:
        if name.upper() not in self._schemas:
            self._schemas.add(name.upper())
            self._add("schemas", {"name": name, "kind": kind})

    def table(self, logical=None, schema="e2e", physical=None, **extra) -> str:
        self.schema(schema)
        logical = logical or f"{TAG} tabla {uuid.uuid4().hex[:4]}"
        return self._add("canonical_tables", {"logicalName": logical, "physicalName": physical or phys(logical),
                                              "schema": schema, **extra})

    def column(self, table_id, logical, dtype="STRING", *, domain=None, pk=False, ordinal=0,
               physical=None, **extra) -> str:
        p = {"tableId": table_id, "logicalName": logical, "physicalName": physical or phys(logical),
             "dataType": dtype, "ordinal": ordinal, "isNullable": not pk, **extra}
        if pk:
            p["isPrimaryKey"] = True
        if domain:
            p["parentDomainId"] = domain
        return self._add("canonical_columns", p)

    def relationship(self, parent_tid, parent_cid, child_tid, child_cid, **extra) -> str:
        return self._add("relationships", {"parentTableId": parent_tid, "childTableId": child_tid,
                                           "pairs": [{"parentColumnId": parent_cid, "childColumnId": child_cid}],
                                           "parentCardinality": "one", "childCardinality": "zero-many",
                                           **extra})

    def view(self, name, table_ids, sources, schema="e2e_vu", **extra) -> str:
        return self._add("views", {"name": name, "schema": schema, "sourceTableIds": list(table_ids),
                                   "sources": sources, **extra})

    def folder(self, name=None) -> str:
        return self._add("folders", {"projectId": self.pid, "name": name or f"{TAG} folder"})

    def canvas(self, name=None, table_ids=(), view_ids=(), folder_id=None, **extra) -> str:
        if folder_id is None:
            self._folder = self._folder or self.folder()
            folder_id = self._folder
        layout = {nid: {"x": 40 + i * 420, "y": 40} for i, nid in enumerate([*table_ids, *view_ids])}
        return self._add("subject_areas", {"projectId": self.pid, "folderId": folder_id,
                                           "name": name or f"{TAG} canvas", "tableIds": list(table_ids),
                                           "viewIds": list(view_ids), "layout": layout, **extra})

    def publish(self, reviewer: Client, title=None) -> str:
        """Escribe lo juntado en UN draft y lo publica. Devuelve el id de la versión."""
        cs = self.owner.draft(self.pid, title or f"{TAG} seed")
        r = self.owner.bulk(cs, self.changes)
        if r.status != 200:
            _fail("seed bulk", r)
        r = self.owner.publish(cs, reviewer, title)
        if r.status != 200 or (r.data or {}).get("status") != "approved":
            _fail("seed publish", r)
        self.changes = []
        return cs


def _fail(op, r: Resp):
    raise RuntimeError(f"fixture {op} falló: {r.status} {str(r.raw)[:300]}")


# ── Limpieza directa en la BD (proyectos registrados + ids sueltos) ──────────
async def _cleanup_async():
    from app.core.db.client import connect, disconnect, get_db
    from app.core.scope import PROJECT_SCOPED
    await connect()
    try:
        db = await get_db()
        pids = _CREATED.get("projects", [])
        if pids:
            cs_ids = [c["_id"] for c in await db["changesets"].find(
                {"projectId": {"$in": pids}}, {"_id": 1}).to_list(None)]
            if cs_ids:
                await db["changeset_changes"].delete_many({"csId": {"$in": cs_ids}})
                # Doc 105 (R2): los jobs de la carga Excel no llevan `projectId`
                # (cuelgan del changeset); el cuerpo usa el id del job.
                job_ids = [j["_id"] for j in await db["upload_jobs"].find(
                    {"csId": {"$in": cs_ids}}, {"_id": 1}).to_list(None)]
                if job_ids:
                    await db["upload_job_bodies"].delete_many({"_id": {"$in": job_ids}})
                    await db["upload_jobs"].delete_many({"_id": {"$in": job_ids}})
            for coll in sorted(PROJECT_SCOPED):
                await db[coll].delete_many({"projectId": {"$in": pids}})
        for coll, ids in _CREATED.items():
            if ids:
                await db[coll].delete_many({"_id": {"$in": ids}})
    finally:
        await disconnect()


def cleanup():
    if _HTTP_FACTORY is not None:
        _CREATED.clear()
        return
    import asyncio
    try:
        asyncio.run(_cleanup_async())
    except Exception as e:  # noqa: BLE001
        print(f"[cleanup] aviso: {e}", file=sys.stderr)
    _CREATED.clear()


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
