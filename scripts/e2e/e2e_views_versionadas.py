"""E2E de VISTAS VERSIONADAS (doc 20) contra el backend vivo.

Ciclo: draft → crear vista vía recordChange sobre una tabla REAL publicada →
el estado efectivo la muestra y producción NO → publish → producción la
muestra → rollback → desaparece de producción.
"""
import sys
import uuid

import httpx

B = "http://localhost:8000"
C = httpx.Client(timeout=60)
FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'OK' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def login(u, p):
    r = C.post(f"{B}/api/auth/login", json={"username": u, "password": p})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


admin, rev = login("admin", "admin"), login("T1238", "T1238")
# Doc 27: rollback restaura A una versión (deshace las posteriores). Para
# deshacer lo publicado por este e2e se rollbackea la versión PREVIA.
prev_ver = C.get(f"{B}/api/versions/published", headers=admin).json()["data"]["id"]
tag = uuid.uuid4().hex[:6]

# 0. Una tabla REAL publicada como fuente
tabs = C.get(f"{B}/api/catalog/tables?q=TIPOESTADOFINANCIEROCUENTA&limit=1", headers=admin).json()["data"]
assert tabs, "no hay tabla fuente"
tid = tabs[0]["id"]

# 1. Draft + vista NUEVA vía recordChange (doc completo)
cs = C.post(f"{B}/api/changesets/snapshot", json={}, headers=admin).json()["data"]["id"]
vid = str(uuid.uuid4())
doc = {
    "id": vid, "name": f"E2E_VU_{tag}", "schema": "e2e_vu", "sql": "",
    "tableId": tid, "sourceTableIds": [tid],
    "sources": [{"tableId": tid, "column": "TIPESTADOFINANCIEROCTA",
                 "outputAlias": "TIPESTADOFINANCIEROCTA"}],
    "showOnCanvas": True,
    "outputAlias": None, "expression": None, "customSql": None, "description": "e2e",
}
r = C.put(f"{B}/api/changesets/{cs}/changes",
          json={"collection": "views", "entityId": vid, "op": "upsert", "payload": doc},
          headers=admin)
check("crear vista en el draft (recordChange)", r.status_code == 200, r.text[:180])

eff = C.get(f"{B}/api/changesets/{cs}/effective/views?tableId={tid}", headers=admin).json()["data"]
check("effective la muestra", any(v["id"] == vid for v in eff))
pub = C.get(f"{B}/api/views?tableId={tid}", headers=admin).json()["data"]
check("producción NO la muestra antes del publish", not any(v["id"] == vid for v in pub))

# 2. Editar la vista DENTRO del draft (upsert doc completo)
doc["description"] = "e2e v2"
r = C.put(f"{B}/api/changesets/{cs}/changes",
          json={"collection": "views", "entityId": vid, "op": "upsert", "payload": doc},
          headers=admin)
check("editar la vista en el draft", r.status_code == 200, r.text[:160])

# 3. Publish → producción la muestra
assert C.post(f"{B}/api/changesets/{cs}/submit",
              json={"reviewers": ["T1238"], "title": "E2E vistas versionadas"},
              headers=admin).status_code == 200
assert C.post(f"{B}/api/changesets/{cs}/approve", json={}, headers=rev).status_code == 200
pub = C.get(f"{B}/api/views?tableId={tid}", headers=admin).json()["data"]
mine = next((v for v in pub if v["id"] == vid), None)
check("publicado: producción la muestra (con la edición)",
      bool(mine) and mine.get("description") == "e2e v2", str(mine)[:160])

# 4. Rollback → desaparece de producción
r = C.post(f"{B}/api/changesets/{prev_ver}/rollback", headers=admin)
check("rollback crea draft inverso", r.status_code == 200, r.text[:200])
if r.status_code == 200:
    inv = r.json()["data"]["id"]
    assert C.post(f"{B}/api/changesets/{inv}/submit",
                  json={"reviewers": ["T1238"], "title": "E2E vistas rollback"},
                  headers=admin).status_code == 200
    assert C.post(f"{B}/api/changesets/{inv}/approve", json={}, headers=rev).status_code == 200
    pub = C.get(f"{B}/api/views?tableId={tid}", headers=admin).json()["data"]
    check("tras el rollback la vista desaparece de producción",
          not any(v["id"] == vid for v in pub))

print(f"\n{'✅ e2e_views_versionadas: todo OK' if not FAILS else '❌ FALLARON: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
