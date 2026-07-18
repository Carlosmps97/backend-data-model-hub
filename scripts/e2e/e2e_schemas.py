"""E2E de la entidad `schemas` versionada (doc 18) contra el backend vivo.

Ciclo: draft → crear esquema (recordChange) → effective lo muestra y
producción NO → rename dentro del draft (0 propagaciones: recién creado) →
delete de un esquema EN USO → 409 → publish → producción lo muestra →
rollback publicado → desaparece de producción.
"""
import sys
import uuid

import httpx

B = "http://localhost:8000"
C = httpx.Client(timeout=60)  # el publish/approve puede superar el default de 5s
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
name = f"e2e_sch_{uuid.uuid4().hex[:6]}"
renamed = f"{name}_v2"
sid = str(uuid.uuid4())

# 1. Draft + crear esquema como cambio del changeset
cs = C.post(f"{B}/api/changesets/snapshot", json={}, headers=admin).json()["data"]["id"]
r = C.put(f"{B}/api/changesets/{cs}/changes",
              json={"collection": "schemas", "entityId": sid, "op": "upsert",
                    "payload": {"name": name, "description": "e2e"}}, headers=admin)
check("crear esquema en el draft (recordChange)", r.status_code == 200, r.text[:160])

eff = C.get(f"{B}/api/changesets/{cs}/effective/schemas", headers=admin).json()["data"]
check("effective muestra el esquema del draft", any(s["name"] == name for s in eff))
pub = C.get(f"{B}/api/schemas", headers=admin).json()["data"]
check("producción NO lo muestra antes del publish", not any(s["name"] == name for s in pub))

# 2. Nombre duplicado → 409
r = C.put(f"{B}/api/changesets/{cs}/changes",
              json={"collection": "schemas", "entityId": str(uuid.uuid4()), "op": "upsert",
                    "payload": {"name": name.upper()}}, headers=admin)
check("duplicado case-insensitive en add_change → 409", r.status_code == 409, r.text[:160])

# 3. Rename dentro del draft (recién creado: 0 tablas/vistas propagadas)
r = C.post(f"{B}/api/changesets/{cs}/schemas/{sid}/rename",
               json={"newName": renamed}, headers=admin)
ok = r.status_code == 200 and r.json()["data"] == {"tables": 0, "views": 0}
check("rename en el draft (propagación 0/0)", ok, r.text[:160])

# 4. Delete de un esquema EN USO (producción) → 409
used = C.get(f"{B}/api/reporting/facets?field=schema&from=tables&limit=1",
                 headers=admin).json()["data"]
used_name = (used[0].get("value") if used and isinstance(used[0], dict) else used[0]) if used else None
if used_name:
    target = next((s for s in pub if s["name"] == used_name), None)
    if target:
        r = C.post(f"{B}/api/changesets/{cs}/schemas/{target['id']}/delete", headers=admin)
        check("delete de esquema en uso → 409", r.status_code == 409, r.text[:160])

# 5. Publish → producción lo muestra (con el nombre renombrado)
assert C.post(f"{B}/api/changesets/{cs}/submit",
                  json={"reviewers": ["T1238"], "title": "E2E schemas"}, headers=admin).status_code == 200
assert C.post(f"{B}/api/changesets/{cs}/approve", json={}, headers=rev).status_code == 200
pub = C.get(f"{B}/api/schemas", headers=admin).json()["data"]
check("publicado: producción muestra el esquema renombrado",
      any(s["name"] == renamed for s in pub) and not any(s["name"] == name for s in pub))

# 6. Rollback de la versión → el esquema desaparece de producción
r = C.post(f"{B}/api/changesets/{cs}/rollback", headers=admin)
check("rollback crea draft inverso", r.status_code == 200, r.text[:200])
draft = r.json()["data"]
assert C.post(f"{B}/api/changesets/{draft['id']}/submit",
                  json={"reviewers": ["T1238"]}, headers=admin).status_code == 200
assert C.post(f"{B}/api/changesets/{draft['id']}/approve", json={}, headers=rev).status_code == 200
pub = C.get(f"{B}/api/schemas", headers=admin).json()["data"]
check("rollback publicado: el esquema ya no está en producción",
      not any(s["name"] in (name, renamed) for s in pub))

print(f"\nRESULTADO: {'TODO OK' if not FAILS else f'FALLARON: {FAILS}'}")
sys.exit(1 if FAILS else 0)
