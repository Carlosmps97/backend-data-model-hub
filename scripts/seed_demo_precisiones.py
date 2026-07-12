"""Seed DEMO del lote "precisiones de modelamiento" (doc 10) — persistente.

Crea vía API (backend vivo en E2E_BASE, default localhost:8000) el mini-modelo
del mock aprobado: proyecto "Demo Precisiones Modelamiento" con CLIENTE /
CUENTA / GARANTIA / RIESGO_CREDITO (schema `banca`), relaciones crowsfoot,
canvas con layout, la vista multi-fuente VW_CLIENTE_360 (CLIENTE+CUENTA,
`showOnCanvas=True`, alias + CAST) y una vista single-source de GARANTIA.
Además: UDP a nivel canvas (2 keys) asignadas al demo y a canvases del corpus
de stress, 2 términos de glosario bloqueados (D4) y `showOnCanvas` activado en
un par de vistas reales migradas.

Idempotente: reusa lo que ya exista (match por schema+physicalName / nombre).
Uso:  .venv/bin/python -m scripts.seed_demo_precisiones
"""
from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
BASE = os.getenv("E2E_BASE", "http://localhost:8000")

_db = MongoClient(os.environ["COSMOS_CONNECTION_STRING"])[
    os.environ.get("COSMOS_DATABASE", "db_modeler")]


def _login(user="admin", pwd="admin") -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=60.0)
    r = c.post("/api/auth/login", json={"username": user, "password": pwd})
    r.raise_for_status()
    body = r.json().get("data") or r.json()
    c.headers["Authorization"] = f"Bearer {body['token']}"
    return c


def _data(r: httpx.Response):
    b = r.json()
    return b.get("data") if isinstance(b, dict) and "data" in b else b


def ensure_table(cli, schema: str, physical: str, logical: str, cols: list[dict]) -> str:
    doc = _db.canonical_tables.find_one(
        {"schema": schema, "physicalName": physical, "flgactive": {"$ne": False}})
    if doc:
        print(f"  = tabla {schema}.{physical} ya existe")
        return doc["_id"]
    r = cli.post("/api/catalog/tables",
                 json={"logicalName": logical, "physicalName": physical, "schema": schema})
    tid = _data(r)["id"]
    for i, col in enumerate(cols):
        cli.post(f"/api/catalog/tables/{tid}/columns", json={**col, "ordinal": i})
    print(f"  + tabla {schema}.{physical} ({len(cols)} columnas)")
    return tid


def col_id(tid: str, physical: str) -> str:
    d = _db.canonical_columns.find_one(
        {"tableId": tid, "physicalName": physical, "flgactive": {"$ne": False}})
    return d["_id"] if d else ""


def ensure_rel(cli, s_tid, s_cid, t_tid, t_cid):
    if _db.relationships.find_one({"sourceTableId": s_tid, "sourceColumnId": s_cid,
                                   "targetTableId": t_tid, "targetColumnId": t_cid,
                                   "flgactive": {"$ne": False}}):
        return
    cli.post("/api/relationships", json={"sourceTableId": s_tid, "sourceColumnId": s_cid,
                                         "targetTableId": t_tid, "targetColumnId": t_cid})


def main():
    admin = _login()

    # ── Proyecto + tablas del mock ────────────────────────────────────────────
    pname = "Demo Precisiones Modelamiento"
    proj = next((p for p in _data(admin.get("/api/projects")) if p["name"] == pname), None)
    pid = proj["id"] if proj else _data(admin.post(
        "/api/projects", json={"name": pname,
                               "description": "Demo del lote doc 10 (vistas en canvas, tipos complejos, UDP de modelo)"}))["id"]
    print(f"proyecto: {pname} ({pid})")

    V = "VARCHAR"
    cliente = ensure_table(admin, "banca", "CLIENTE", "cliente", [
        {"logicalName": "id cliente", "physicalName": "id_cliente", "dataType": "BIGINT",
         "isPrimaryKey": True, "isNullable": False},
        {"logicalName": "nombre cliente", "physicalName": "nombre_cliente", "dataType": f"{V}(120)"},
        {"logicalName": "tipo documento", "physicalName": "tipo_documento", "dataType": f"{V}(8)"},
        {"logicalName": "numero documento", "physicalName": "num_documento", "dataType": f"{V}(20)"},
        {"logicalName": "segmento", "physicalName": "segmento", "dataType": f"{V}(24)"},
        # #6: tipo complejo (se colapsa en el canvas con tooltip del tipo completo)
        {"logicalName": "contacto", "physicalName": "contacto",
         "dataType": "STRUCT<telefono:STRING, email:STRING, direccion:STRUCT<calle:STRING, ciudad:STRING>>"},
    ])
    cuenta = ensure_table(admin, "banca", "CUENTA", "cuenta", [
        {"logicalName": "id cuenta", "physicalName": "id_cuenta", "dataType": "BIGINT",
         "isPrimaryKey": True, "isNullable": False},
        {"logicalName": "id cliente", "physicalName": "id_cliente", "dataType": "BIGINT",
         "isForeignKey": True},
        {"logicalName": "numero cuenta", "physicalName": "num_cuenta", "dataType": f"{V}(20)"},
        {"logicalName": "saldo actual", "physicalName": "saldo_actual", "dataType": "DECIMAL(18,2)"},
        {"logicalName": "estado", "physicalName": "estado", "dataType": f"{V}(12)"},
        {"logicalName": "fecha apertura", "physicalName": "fecha_apertura", "dataType": "DATE"},
        {"logicalName": "movimientos recientes", "physicalName": "movimientos_recientes",
         "dataType": "ARRAY<STRUCT<fecha:DATE, monto:DECIMAL(18,2), canal:STRING>>"},
    ])
    garantia = ensure_table(admin, "banca", "GARANTIA", "garantia", [
        {"logicalName": "id garantia", "physicalName": "id_garantia", "dataType": "BIGINT",
         "isPrimaryKey": True, "isNullable": False},
        {"logicalName": "id cliente", "physicalName": "id_cliente", "dataType": "BIGINT",
         "isForeignKey": True},
        {"logicalName": "tipo garantia", "physicalName": "tipo_garantia", "dataType": f"{V}(24)"},
        {"logicalName": "valor tasado", "physicalName": "valor_tasado", "dataType": "DECIMAL(18,2)"},
        {"logicalName": "moneda", "physicalName": "moneda", "dataType": "CHAR(3)"},
    ])
    riesgo = ensure_table(admin, "banca", "RIESGO_CREDITO", "riesgo credito", [
        {"logicalName": "id riesgo", "physicalName": "id_riesgo", "dataType": "BIGINT",
         "isPrimaryKey": True, "isNullable": False},
        {"logicalName": "id cuenta", "physicalName": "id_cuenta", "dataType": "BIGINT",
         "isForeignKey": True},
        {"logicalName": "score riesgo", "physicalName": "score_riesgo", "dataType": "INT"},
        {"logicalName": "probabilidad incumplimiento", "physicalName": "prob_incumplimiento",
         "dataType": "DECIMAL(9,6)"},
        {"logicalName": "exposicion", "physicalName": "exposicion", "dataType": "DECIMAL(18,2)"},
        {"logicalName": "fecha calculo", "physicalName": "fecha_calculo", "dataType": "DATE"},
    ])

    ensure_rel(admin, cuenta, col_id(cuenta, "id_cliente"), cliente, col_id(cliente, "id_cliente"))
    ensure_rel(admin, garantia, col_id(garantia, "id_cliente"), cliente, col_id(cliente, "id_cliente"))
    ensure_rel(admin, riesgo, col_id(riesgo, "id_cuenta"), cuenta, col_id(cuenta, "id_cuenta"))
    print("  relaciones OK (CUENTA→CLIENTE, GARANTIA→CLIENTE, RIESGO→CUENTA)")

    # ── Canvas + layout (espejo del mock) ────────────────────────────────────
    cname = "Modelo Cliente 360"
    sa = _db.subject_areas.find_one({"projectId": pid, "name": cname})
    said = sa["_id"] if sa else _data(admin.post(
        "/api/subject-areas", json={"projectId": pid, "name": cname}))["id"]
    admin.put(f"/api/subject-areas/{said}/tables",
              json={"tableIds": [cliente, cuenta, garantia, riesgo]})
    print(f"canvas: {cname} ({said})")

    # ── Vistas (#3-5): multi-fuente con alias+CAST y single-source ──────────
    vw = _db.views.find_one({"name": "VW_CLIENTE_360", "flgactive": {"$ne": False}})
    if not vw:
        r = admin.post("/api/views", json={
            "name": "VW_CLIENTE_360", "schema": "banca_vw",
            "description": "Vista 360 del cliente (demo lote doc 10)",
            "sql": "",
            "sourceTableIds": [cliente, cuenta],
            "showOnCanvas": True,
            "sources": [
                {"tableId": cliente, "column": "id_cliente", "outputAlias": "id_cliente"},
                {"tableId": cliente, "column": "nombre_cliente", "outputAlias": "nombre_cliente"},
                {"tableId": cliente, "column": "segmento", "outputAlias": "segmento"},
                {"tableId": cuenta, "column": "saldo_actual", "outputAlias": "saldo_total",
                 "castType": "DECIMAL(18,2)", "expression": "SUM(t2.saldo_actual)"},
            ]})
        vw_id = _data(r)["id"]
        print(f"  + vista VW_CLIENTE_360 multi-fuente ({vw_id}) showOnCanvas=True")
    else:
        vw_id = vw["_id"]
        print("  = vista VW_CLIENTE_360 ya existe")

    vg = _db.views.find_one({"name": "VW_GARANTIA_VIGENTE", "flgactive": {"$ne": False}})
    if not vg:
        r = admin.post("/api/views", json={
            "name": "VW_GARANTIA_VIGENTE", "schema": "banca_vw", "sql": "",
            "description": "Garantías vigentes (demo)",
            "sourceTableIds": [garantia], "showOnCanvas": True,
            "filter": "moneda = 'PEN'",
            "sources": [
                {"tableId": garantia, "column": "id_garantia", "outputAlias": "id_garantia"},
                {"tableId": garantia, "column": "tipo_garantia", "outputAlias": "tipo"},
                {"tableId": garantia, "column": "valor_tasado", "outputAlias": "valor_pen"},
            ]})
        vg_id = _data(r)["id"]
        print(f"  + vista VW_GARANTIA_VIGENTE ({vg_id}) showOnCanvas=True")
    else:
        vg_id = vg["_id"]

    # layout espejo del mock: tablas + NODOS DE VISTA (posición por canvas)
    admin.put(f"/api/subject-areas/{said}/layout", json={"layout": {
        cliente: {"x": 40, "y": 40}, cuenta: {"x": 560, "y": 20},
        garantia: {"x": 40, "y": 420}, riesgo: {"x": 560, "y": 380},
        vw_id: {"x": 620, "y": 700}, vg_id: {"x": 60, "y": 740},
    }})

    # ── UDP a nivel canvas (#10) ─────────────────────────────────────────────
    defs = {d["name"]: d for d in _data(admin.get("/api/udp"))}
    ups = []
    if "Dominio de Negocio" not in defs:
        ups.append({"name": "Dominio de Negocio", "level": "canvas", "dataType": "list",
                    "allowedValues": ["Clientes", "Riesgos", "Finanzas", "Operaciones"]})
    if "Criticidad del Modelo" not in defs:
        ups.append({"name": "Criticidad del Modelo", "level": "canvas", "dataType": "list",
                    "allowedValues": ["Alta", "Media", "Baja"], "defaultValue": "Media"})
    if ups:
        admin.post("/api/standards/apply",
                   json={"kind": "udp", "title": "UDP de Modelo (demo doc 10)", "udpUpsert": ups})
        defs = {d["name"]: d for d in _data(admin.get("/api/udp"))}
        print(f"  + defs UDP canvas: {[u['name'] for u in ups]}")
    dom_id, cri_id = defs["Dominio de Negocio"]["id"], defs["Criticidad del Modelo"]["id"]

    admin.put(f"/api/subject-areas/{said}/udp",
              json={"udpValues": {dom_id: "Clientes", cri_id: "Alta"}})
    # y a 6 canvases del corpus de stress, valores variados (para reportes)
    doms, cris = ["Riesgos", "Finanzas", "Operaciones"], ["Alta", "Media", "Baja"]
    stress = list(_db.subject_areas.find({"name": {"$regex": "^Canvas "}},
                                         {"_id": 1, "name": 1}).limit(6))
    for i, sdoc in enumerate(stress):
        admin.put(f"/api/subject-areas/{sdoc['_id']}/udp",
                  json={"udpValues": {dom_id: doms[i % 3], cri_id: cris[i % 3]}})
    print(f"  UDP asignado al demo + {len(stress)} canvases de stress")

    # ── Glosario: 2 términos bloqueados (D4, se ven 'plomo' en el front) ─────
    locked = 0
    for term in _db.glossary_terms.find({"locked": {"$ne": True},
                                         "term": {"$not": {"$regex": "^E2E_"}}}).sort("term", 1).limit(2):
        r = admin.post(f"/api/glossary/{term['_id']}/lock")
        locked += 1 if r.status_code == 200 else 0
        print(f"  lock glosario: {term.get('term')} → {term.get('abbrev')}")
    print(f"  términos bloqueados ahora: {_db.glossary_terms.count_documents({'locked': True})}")

    # ── showOnCanvas en vistas reales migradas de un canvas de stress ────────
    big = _db.subject_areas.find_one({"name": {"$regex": "^Canvas 42 "}}) or (stress[0] if stress else None)
    if big:
        tids = list(big.get("tableIds") or [])
        flipped = 0
        for v in _db.views.find({"sourceTableIds": {"$in": tids},
                                 "flgactive": {"$ne": False}}).limit(3):
            body = {k: v.get(k) for k in ("name", "sql", "description", "tableId", "tags",
                                          "filter", "sources", "sourceTableIds", "joinOverride")}
            body["schema"] = v.get("schema")
            body["showOnCanvas"] = True
            r = admin.put(f"/api/views/{v['_id']}", json=body)
            flipped += 1 if r.status_code == 200 else 0
        print(f"  showOnCanvas=True en {flipped} vistas reales de '{big.get('name')}'")

    # ── Verificación final ───────────────────────────────────────────────────
    diag = _data(admin.get(f"/api/subject-areas/{said}/diagram"))
    print("\nVERIFICACIÓN diagrama demo:",
          f"tablas={len(diag.get('tables') or [])}",
          f"relaciones={len(diag.get('relationships') or [])}",
          f"vistas={[x.get('name') for x in diag.get('views') or []]}")
    ok = (len(diag.get("tables") or []) == 4
          and {x.get("name") for x in diag.get("views") or []} >= {"VW_CLIENTE_360", "VW_GARANTIA_VIGENTE"})
    print("SEED", "OK" if ok else "INCOMPLETO")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
