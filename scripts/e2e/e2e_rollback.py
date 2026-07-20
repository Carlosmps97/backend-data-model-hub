"""E2E del rollback de versiones (semántica doc 27) contra el backend vivo.

Ciclo: publicar un cambio (captura imágenes previas) → rollback → draft
inverso → aprobar el rollback → la entidad vuelve a su estado original.
También verifica el 409 de una versión publicada SIN imágenes previas.
"""
import sys, uuid
import httpx

# WAN (Lakebase/Cosmos remotos): publish/approve pueden superar el default de
# 5 s de httpx — todas las llamadas van con timeout explícito.
def _get(url, **k):
    k.setdefault("timeout", 60)
    return httpx.request("GET", url, **k)


def _post(url, **k):
    k.setdefault("timeout", 60)
    return httpx.request("POST", url, **k)


def _put(url, **k):
    k.setdefault("timeout", 60)
    return httpx.request("PUT", url, **k)


B = "http://localhost:8000"
FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'OK' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def login(u, p):
    r = _post(f"{B}/api/auth/login", json={"username": u, "password": p})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def publish(admin, rev, changes, title):
    r = _post(f"{B}/api/changesets/snapshot", json={}, headers=admin)
    cs = r.json()["data"]["id"]
    for coll, eid, op, payload in changes:
        rr = _put(f"{B}/api/changesets/{cs}/changes",
                       json={"collection": coll, "entityId": eid, "op": op, "payload": payload},
                       headers=admin)
        assert rr.status_code == 200, rr.text
    assert _post(f"{B}/api/changesets/{cs}/submit",
                      json={"reviewers": ["T1238"], "title": title}, headers=admin).status_code == 200
    assert _post(f"{B}/api/changesets/{cs}/approve", json={}, headers=rev).status_code == 200
    return cs


admin, rev = login("admin", "admin"), login("T1238", "T1238")

# 0. Rollback de la versión en producción ACTUAL (doc 27): restaurar A la
#    versión vigente no tiene nada posterior que deshacer → 409 claro.
prod = _get(f"{B}/api/versions/published", headers=admin).json()["data"]
r = _post(f"{B}/api/changesets/{prod['id']}/rollback", headers=admin)
check("rollback de la versión vigente → 409 (nada que deshacer)",
      r.status_code == 409 and "current production" in r.json().get("detail", ""),
      r.text[:160])
prev1 = prod["id"]  # la versión a la que se restaurará para deshacer cs1

# 1. Publicar una versión con: tabla EXISTENTE modificada + tabla NUEVA
tbl = _get(f"{B}/api/catalog/tables?q=LPR_&limit=1", headers=admin).json()["data"][0]
orig_desc = tbl.get("description")
new_id = str(uuid.uuid4())
cs1 = publish(admin, rev, [
    ("canonical_tables", tbl["id"], "upsert", {**{k: v for k, v in tbl.items() if k != "id"},
                                               "description": "DESC MODIFICADA POR E2E"}),
    ("canonical_tables", new_id, "upsert", {"physicalName": "TB_E2E_ROLLBACK",
                                            "logicalName": "Tb E2E Rollback", "schema": "e2e_schema"}),
], "E2E cambio a revertir")
t = _get(f"{B}/api/catalog/tables?q={tbl['physicalName']}&limit=1", headers=admin).json()["data"][0]
check("cambio publicado", t.get("description") == "DESC MODIFICADA POR E2E")

# 2. Rollback (doc 27): restaurar A la versión previa ⇒ draft inverso de cs1
r = _post(f"{B}/api/changesets/{prev1}/rollback", headers=admin)
check("rollback crea draft", r.status_code == 200, r.text[:200])
draft = r.json()["data"]
check("draft en estado draft", draft["status"] == "draft" and draft["title"].startswith("Rollback"))

# 3. Aprobar el rollback por el flujo normal
assert _post(f"{B}/api/changesets/{draft['id']}/submit",
                  json={"reviewers": ["T1238"]}, headers=admin).status_code == 200
rr = _post(f"{B}/api/changesets/{draft['id']}/approve", json={}, headers=rev)
check("rollback aprobado/publicado", rr.status_code == 200, rr.text[:200])

# 4. Producción restaurada
t = _get(f"{B}/api/catalog/tables?q={tbl['physicalName']}&limit=1", headers=admin).json()["data"][0]
check("descripción restaurada al original", t.get("description") == orig_desc,
      f"esperado={orig_desc!r} actual={t.get('description')!r}")
gone = _get(f"{B}/api/catalog/tables?q=TB_E2E_ROLLBACK", headers=admin).json()["data"]
check("tabla creada en la versión → eliminada por el rollback", not gone)

# 5. Doc 27: la versión VIGENTE (el rollback recién publicado) no tiene nada
#    posterior que deshacer → 409. (cs1, no-última, ahora SÍ sería objetivo válido.)
cur = _get(f"{B}/api/versions/published", headers=admin).json()["data"]
r = _post(f"{B}/api/changesets/{cur['id']}/rollback", headers=admin)
check("rollback de la versión vigente tras publicar → 409", r.status_code == 409)

print("\nRESULTADO:", "TODO OK" if not FAILS else f"FALLARON: {FAILS}")
sys.exit(1 if FAILS else 0)
