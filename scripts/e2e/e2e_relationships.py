"""E2E de relaciones v2 (doc 19) contra el backend vivo.

Ciclo: draft → 2 tablas con columnas → relación COMPUESTA (2 pares,
identifying) vía recordChange → effective la muestra en shape v2 y producción
NO → payload LEGACY (source/target) se normaliza al grabarse → toggle a
non-identifying → publish → producción v2 → impact por columna de un par →
rollback → desaparece.
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


def rec(cs, headers, collection, eid, payload=None, op="upsert"):
    return C.put(f"{B}/api/changesets/{cs}/changes",
                 json={"collection": collection, "entityId": eid, "op": op,
                       **({"payload": payload} if payload is not None else {})},
                 headers=headers)


admin, rev = login("admin", "admin"), login("T1238", "T1238")
tag = uuid.uuid4().hex[:6]

# 1. Draft + 2 tablas con 2 columnas cada una (padre con PK compuesta)
cs = C.post(f"{B}/api/changesets/snapshot", json={}, headers=admin).json()["data"]["id"]
tp, tc = f"e2e-relp-{tag}", f"e2e-relc-{tag}"
p1, p2, c1, c2 = (f"{tp}.a", f"{tp}.b", f"{tc}.a", f"{tc}.b")
for tid, name in ((tp, f"E2E_REL_PADRE_{tag}"), (tc, f"E2E_REL_HIJO_{tag}")):
    r = rec(cs, admin, "canonical_tables", tid,
            {"physicalName": name, "logicalName": name.lower(), "schema": "e2e"})
    assert r.status_code == 200, r.text
for cid, tid, name, pk in ((p1, tp, "COD_A", True), (p2, tp, "COD_B", True),
                           (c1, tc, "COD_A", False), (c2, tc, "COD_B", False)):
    r = rec(cs, admin, "canonical_columns", cid,
            {"tableId": tid, "physicalName": name, "logicalName": name.lower(),
             "dataType": "STRING", "isPrimaryKey": pk, "ordinal": 0})
    assert r.status_code == 200, r.text

# 2. Relación COMPUESTA identifying (2 pares) en el draft
rid = str(uuid.uuid4())
r = rec(cs, admin, "relationships", rid, {
    "parentTableId": tp, "childTableId": tc,
    "pairs": [{"parentColumnId": p1, "childColumnId": c1},
              {"parentColumnId": p2, "childColumnId": c2, "roleName": "rol_b"}],
    "parentCardinality": "one", "childCardinality": "zero-many", "identifying": True,
})
check("crear relación compuesta v2 en el draft", r.status_code == 200, r.text[:160])

eff = C.get(f"{B}/api/changesets/{cs}/effective/relationships?tableId={tp}",
            headers=admin).json()["data"]
mine = next((x for x in eff if x["id"] == rid), None)
check("effective la muestra en shape v2 (2 pares + roleName)",
      bool(mine) and len(mine.get("pairs", [])) == 2
      and mine["pairs"][1].get("roleName") == "rol_b"
      and mine.get("parentTableId") == tp and mine.get("identifying") is True,
      str(mine)[:200])
pub = C.get(f"{B}/api/relationships", headers=admin).json()["data"]
check("producción NO la muestra antes del publish", not any(x["id"] == rid for x in pub))

# 3. Payload LEGACY (source/target) → add_change lo normaliza a v2
rid_leg = str(uuid.uuid4())
r = rec(cs, admin, "relationships", rid_leg, {
    "sourceTableId": tc, "sourceColumnId": c1, "targetTableId": tp, "targetColumnId": p1,
    "sourceCardinality": "many", "targetCardinality": "zero-many", "identifying": False,
})
eff = C.get(f"{B}/api/changesets/{cs}/effective/relationships?tableId={tp}",
            headers=admin).json()["data"]
leg = next((x for x in eff if x["id"] == rid_leg), None)
check("payload legacy queda normalizado (hijo=source, child zero-many)",
      r.status_code == 200 and bool(leg) and leg.get("childTableId") == tc
      and leg.get("parentTableId") == tp and leg.get("childCardinality") == "zero-many"
      and leg.get("pairs") and leg["pairs"][0]["childColumnId"] == c1,
      str(leg)[:200])
assert rec(cs, admin, "relationships", rid_leg, op="delete").status_code == 200

# 4. Toggle a non-identifying (upsert doc completo)
mine["identifying"] = False
r = rec(cs, admin, "relationships", rid, {k: v for k, v in mine.items() if k != "id"} | {"id": rid})
check("toggle a non-identifying en el draft", r.status_code == 200, r.text[:160])

# 5. Publish → producción en v2
assert C.post(f"{B}/api/changesets/{cs}/submit",
              json={"reviewers": ["T1238"], "title": "E2E relaciones v2"}, headers=admin).status_code == 200
assert C.post(f"{B}/api/changesets/{cs}/approve", json={}, headers=rev).status_code == 200
pub = C.get(f"{B}/api/relationships?tableId={tp}", headers=admin).json()["data"]
mine = next((x for x in pub if x["id"] == rid), None)
check("publicado: producción v2 con 2 pares y non-identifying",
      bool(mine) and len(mine.get("pairs", [])) == 2 and mine.get("identifying") is False,
      str(mine)[:200])

# 6. Impact por columna de un par (extremo padre)
imp = C.get(f"{B}/api/relationships/impact?columnId={p2}", headers=admin).json()["data"]
row = next((x for x in imp.get("relationships", []) if x["relId"] == rid), None)
check("impact por columna del par: thisSide=parent y otherColumn=hija",
      bool(row) and row.get("thisSide") == "parent" and row.get("otherColumnId") == c2,
      str(row)[:200])

# 7. Rollback → desaparece de producción
r = C.post(f"{B}/api/changesets/{cs}/rollback", headers=admin)
check("rollback crea draft inverso", r.status_code == 200, r.text[:200])
if r.status_code == 200:
    inv = r.json()["data"]["id"]
    assert C.post(f"{B}/api/changesets/{inv}/submit",
                  json={"reviewers": ["T1238"], "title": "E2E rel rollback"}, headers=admin).status_code == 200
    assert C.post(f"{B}/api/changesets/{inv}/approve", json={}, headers=rev).status_code == 200
    pub = C.get(f"{B}/api/relationships?tableId={tp}", headers=admin).json()["data"]
    check("tras el rollback la relación desaparece de producción",
          not any(x["id"] == rid for x in pub))

print(f"\n{'✅ e2e_relationships: todo OK' if not FAILS else '❌ FALLARON: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
