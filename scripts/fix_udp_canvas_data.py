"""Saneo puntual post-auditoría (C8, 2026-07-12) — one-shot, idempotente.

Causa raíz: el seed de demo asignó a los canvases la def UDP preexistente
"Dominio de Negocio" que es `level=table` (la buscó por nombre sin chequear
nivel) → 7 canvases con una key de nivel equivocado. Además los rollbacks de
corridas E2E resucitaron una def `E2E_* Criticidad` y dejaron versiones y
snapshots polucionados en `standards_versions`.

Pasos (vía API cuando existe endpoint; Mongo directo solo para historial):
  1. Crea la def CANVAS "Dominio de Negocio" vía POST /standards/apply
     (versionada) si no existe una con ese nombre Y level=canvas.
  2. Remapea en los canvases la key vieja (def table) → la nueva def canvas
     vía PUT /api/subject-areas/{id}/udp (audita, mismo régimen del front).
  3. Borra defs residuales `E2E_*` de udp_definitions.
  4. Purga versiones E2E del historial (title/label E2E_) y SCRUBEA entradas
     E2E de los snapshots restantes (udp + dict) para que ningún rollback
     futuro las resucite.

Uso: .venv/bin/python -m scripts.fix_udp_canvas_data
"""
from __future__ import annotations

import os
import re
import sys

import httpx
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne

load_dotenv()
BASE = os.getenv("E2E_BASE", "http://localhost:8000")
db = MongoClient(os.environ["COSMOS_CONNECTION_STRING"])[
    os.environ.get("COSMOS_DATABASE", "db_modeler")]
ACTIVE = {"flgactive": {"$ne": False}}
# El tag puede ir en medio ("UDP E2E_xxx Criticidad") — search, no match.
E2E_RX = re.compile(r"E2E_[0-9a-f]{8}")


def _login() -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=60.0)
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    r.raise_for_status()
    c.headers["Authorization"] = f"Bearer {(r.json().get('data') or r.json())['token']}"
    return c


def _data(r: httpx.Response):
    b = r.json()
    return b.get("data") if isinstance(b, dict) and "data" in b else b


def main():
    admin = _login()

    # 1) def canvas "Dominio de Negocio" (por nombre Y nivel)
    defs = _data(admin.get("/api/udp")) or []
    table_dom = next((d for d in defs if d["name"] == "Dominio de Negocio"
                      and d["level"] == "table"), None)
    canvas_dom = next((d for d in defs if d["name"] == "Dominio de Negocio"
                       and d["level"] == "canvas"), None)
    if not canvas_dom:
        r = admin.post("/api/standards/apply", json={
            "kind": "udp", "title": "UDP de Modelo: Dominio de Negocio (fix C8)",
            "udpUpsert": [{"name": "Dominio de Negocio", "level": "canvas",
                           "dataType": "list",
                           "allowedValues": ["Clientes", "Riesgos", "Finanzas", "Operaciones"]}]})
        assert r.status_code == 200, f"apply falló: {r.status_code} {r.text[:200]}"
        canvas_dom = next(d for d in (_data(admin.get("/api/udp")) or [])
                          if d["name"] == "Dominio de Negocio" and d["level"] == "canvas")
        print(f"[1] def canvas creada: {canvas_dom['id']}")
    else:
        print(f"[1] def canvas ya existía: {canvas_dom['id']}")

    # 2) remap key vieja → nueva en canvases (vía PUT /udp, audita)
    old_id = (table_dom or {}).get("id")
    remapped = 0
    if old_id:
        for sa in db.subject_areas.find({f"udpValues.{old_id}": {"$exists": True}},
                                        {"_id": 1, "udpValues": 1}):
            vals = dict(sa.get("udpValues") or {})
            vals[canvas_dom["id"]] = vals.pop(old_id)
            r = admin.put(f"/api/subject-areas/{sa['_id']}/udp", json={"udpValues": vals})
            remapped += 1 if r.status_code == 200 else 0
    print(f"[2] canvases remapeados (table-def → canvas-def): {remapped}")

    # 3) defs E2E residuales
    res = db.udp_definitions.delete_many({"name": {"$regex": "E2E_[0-9a-f]{8}"}})
    print(f"[3] defs E2E_ borradas: {res.deleted_count}")

    # 4) historial: purga versiones E2E + scrub de snapshots
    purged = db.standards_versions.delete_many(
        {"$or": [{"title": E2E_RX}, {"label": E2E_RX}]}).deleted_count
    ops = []
    for v in db.standards_versions.find({}, {"_id": 1, "snapshot": 1}):
        snap = v.get("snapshot") or {}
        upd = {}
        for key in ("udp", "dict"):
            items = snap.get(key) or []
            clean = [x for x in items
                     if not E2E_RX.match(str(x.get("name") or x.get("term") or ""))]
            if len(clean) != len(items):
                upd[f"snapshot.{key}"] = clean
        if upd:
            ops.append(UpdateOne({"_id": v["_id"]}, {"$set": upd}))
    if ops:
        db.standards_versions.bulk_write(ops, ordered=False)
    print(f"[4] versiones E2E purgadas: {purged} · snapshots scrubeados: {len(ops)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
