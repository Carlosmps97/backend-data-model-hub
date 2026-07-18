"""E2E del fix 'estructura versionada' (doc 16) contra el backend vivo.

Flujo: admin crea versión (snapshot) → registra proyecto+folder+canvas+tabla+
columna EN el changeset → producción NO los ve → el estado efectivo SÍ →
submit con revisor → revisor aprueba → producción los ve.
"""
import sys, uuid
import httpx

B = "http://localhost:8000"
FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'OK' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def login(user, pwd):
    r = httpx.post(f"{B}/api/auth/login", json={"username": user, "password": pwd})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def get(h, path):
    r = httpx.get(f"{B}{path}", headers=h)
    return r.status_code, (r.json().get("data") if r.headers.get("content-type", "").startswith("application/json") else None)


admin = login("admin", "admin")

# 1. Nueva versión desde producción
r = httpx.post(f"{B}/api/changesets/snapshot", json={}, headers=admin)
r.raise_for_status()
cs = r.json()["data"]["id"]
print(f"changeset draft: {cs}")

# 2. Estructura + modelo DENTRO del changeset
# Nombre RANDOMIZADO: el e2e publica de verdad, así que un nombre fijo choca
# con el dup-check (409) en la corrida siguiente (residuo stateful).
pid, fid, said, tid, cid = (str(uuid.uuid4()) for _ in range(5))
TB = f"TB_E2E_DRAFT_{uuid.uuid4().hex[:6].upper()}"
changes = [
    ("projects", pid, {"id": pid, "name": "Proyecto E2E Draft"}),
    ("folders", fid, {"id": fid, "projectId": pid, "parentFolderId": None, "name": "Dominio E2E", "order": 0}),
    ("canonical_tables", tid, {"id": tid, "physicalName": TB, "logicalName": TB.lower().replace("_", " "), "schema": "e2e_schema"}),
    ("canonical_columns", cid, {"id": cid, "tableId": tid, "physicalName": "CODE2E", "logicalName": "Codigo E2E", "dataType": "INT", "ordinal": 0}),
    ("subject_areas", said, {"id": said, "projectId": pid, "folderId": fid, "name": "Canvas E2E",
                             "tableIds": [tid], "layout": {tid: {"x": 80, "y": 80}}, "drawings": [], "udpValues": {}}),
]
for coll, eid, payload in changes:
    r = httpx.put(f"{B}/api/changesets/{cs}/changes",
                  json={"collection": coll, "entityId": eid, "op": "upsert", "payload": payload}, headers=admin)
    check(f"registrar {coll}", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")

# 3. PRODUCCIÓN no debe verlos
sc, projs = get(admin, "/api/projects")
check("producción: proyecto draft INVISIBLE", all(p["id"] != pid for p in projs))
sc, _ = get(admin, f"/api/subject-areas/{said}/diagram")
check("producción: diagrama del canvas draft → 404", sc == 404)
sc, tabs = get(admin, f"/api/catalog/tables?q={TB}")
check("producción: tabla draft INVISIBLE en catálogo", not tabs)

# 4. Estado EFECTIVO del changeset sí
sc, eff = get(admin, f"/api/changesets/{cs}/effective/projects")
check("efectivo: proyecto draft visible", any(p["id"] == pid for p in (eff or [])))
sc, dia = get(admin, f"/api/subject-areas/{said}/diagram?changesetId={cs}")
check("efectivo: diagrama draft renderiza", sc == 200 and dia and len(dia["tables"]) == 1
      and dia["tables"][0]["physicalName"] == TB
      and dia["subjectArea"]["layout"].get(tid) is not None,
      f"sc={sc}")

# 5. Submit con revisor y aprobación
r = httpx.post(f"{B}/api/changesets/{cs}/submit",
               json={"reviewers": ["T1238"], "title": "E2E estructura versionada"}, headers=admin)
check("submit", r.status_code == 200, r.text[:200])
rev = login("T1238", "T1238")
r = httpx.post(f"{B}/api/changesets/{cs}/approve", json={}, headers=rev)
check("approve (revisor)", r.status_code == 200, r.text[:200])

# 6. PRODUCCIÓN ahora sí
sc, projs = get(admin, "/api/projects")
check("publicado: proyecto visible en producción", any(p["id"] == pid for p in projs))
sc, dia = get(admin, f"/api/subject-areas/{said}/diagram")
check("publicado: diagrama renderiza SIN changeset", sc == 200 and dia and len(dia["tables"]) == 1)
sc, folders = get(admin, f"/api/projects/{pid}/folders")
check("publicado: folder visible", any(f["id"] == fid for f in (folders or [])))

# 7. AUTO-LIMPIEZA: rollback de la versión publicada por este e2e — la BD real
# no debe acumular proyectos/tablas de prueba tras cada corrida (doc 20).
r = httpx.post(f"{B}/api/changesets/{cs}/rollback", headers=admin)
check("cleanup: rollback del e2e", r.status_code == 200, r.text[:160])
if r.status_code == 200:
    inv = r.json()["data"]["id"]
    ok1 = httpx.post(f"{B}/api/changesets/{inv}/submit",
                     json={"reviewers": ["T1238"], "title": "E2E estructura rollback"},
                     headers=admin).status_code == 200
    ok2 = httpx.post(f"{B}/api/changesets/{inv}/approve", json={}, headers=rev).status_code == 200
    sc, projs = get(admin, "/api/projects")
    check("cleanup: proyecto e2e fuera de producción",
          ok1 and ok2 and all(p["id"] != pid for p in projs))

print("\nRESULTADO:", "TODO OK" if not FAILS else f"FALLARON: {FAILS}")
sys.exit(1 if FAILS else 0)
