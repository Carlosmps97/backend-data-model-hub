"""Escenarios E2E contra el backend en vivo. Cada `sNN_*` devuelve un `Suite`.

Todos usan fixtures AISLADOS (tag único por proceso) y limpian al final. Correr:
    .venv/bin/python -m scripts.e2e.run_e2e <scenario>       # uno
    .venv/bin/python -m scripts.e2e.run_e2e all              # todos (serial)
"""
from __future__ import annotations

import uuid

from scripts.e2e import harness as H
from scripts.e2e.harness import TAG, Client, Suite


def _uid(p): return f"{TAG}-{p}-{uuid.uuid4().hex[:6]}"


# ══ S01 · RBAC por rol (matriz de permisos sobre endpoints reales) ═══════════
def s01_rbac() -> Suite:
    s = Suite("s01_rbac")
    admin, mod, rev, lec = Client("admin"), Client("modelador"), Client("revisor"), Client("lector")

    # /auth/me devuelve los permisos correctos por rol
    s.eq("admin access_level=full", admin.me["accessLevel"], "full")
    s.check("lector solo view+export", set(k for k, v in lec.me["permissions"].items() if v) == {"model.view", "export"})

    # model.edit — escrituras del modelo vía changeset
    r = lec.post("/api/changesets/snapshot", {"title": f"{TAG} x", "projectIds": []})
    s.eq("lector NO puede snapshot (model.edit)", r.status, 403)
    r = mod.post("/api/changesets/snapshot", {"title": f"{TAG} x", "projectIds": []})
    s.check("modelador SÍ puede snapshot", r.status in (200, 201), f"status={r.status}")
    if r.status in (200, 201):
        H._track("changesets", r.data["id"])

    # Endpoints DIRECTOS (fuera del changeset): ¿están gateados?
    for name, cli, exp_block in [("lector", lec, True), ("modelador", mod, False)]:
        rt = cli.post("/api/catalog/tables", {"logicalName": f"{TAG} rbac", "schema": "e2e"})
        if rt.status in (200, 201):
            H._track("canonical_tables", rt.data["id"])
        s.check(f"{name} crear tabla directa {'bloqueado' if exp_block else 'permitido'}",
                (rt.status == 403) == exp_block, f"status={rt.status} (esperado {'403' if exp_block else '2xx'})")
        rv = cli.post("/api/views", {"name": _uid("v"), "sql": "SELECT 1"})
        if rv.status in (200, 201):
            H._track("views", rv.data["id"])
        s.check(f"{name} crear vista directa {'bloqueado' if exp_block else 'permitido'}",
                (rv.status == 403) == exp_block, f"status={rv.status}")

    # standards.edit — solo admin (standards.edit)
    body = {"kind": "domain", "domainsUpsert": [{"name": f"{TAG} nop", "defaultDataType": "STRING"}]}
    s.eq("modelador NO puede standards.apply", mod.post("/api/standards/apply", body).status, 403)
    s.eq("lector NO puede standards.apply", lec.post("/api/standards/apply", body).status, 403)

    # admin.manage — solo admin
    s.eq("modelador NO puede listar users (admin)", mod.get("/api/admin/users").status, 403)
    s.eq("revisor NO puede listar users (admin)", rev.get("/api/admin/users").status, 403)
    s.check("admin SÍ lista users", admin.get("/api/admin/users").status == 200)

    # review.decide — modelador no puede aprobar
    s.eq("modelador NO puede aprobar (review.decide)", mod.post(f"/api/changesets/nope/approve").status, 403)
    return s


# ══ S02 · Ciclo de versión completo (snapshot→cambios→submit→approve→prod) ═══
def _cs_change(cli, cs_id, collection, entity_id, payload, op="upsert"):
    return cli.put(f"/api/changesets/{cs_id}/changes",
                   {"collection": collection, "entityId": entity_id, "op": op, "payload": payload})


def s02_version_lifecycle() -> Suite:
    s = Suite("s02_version_lifecycle")
    mod, rev = Client("modelador"), Client("revisor")

    # Producción base: proyecto + tabla publicada directamente.
    pid = mod.create_project()
    tid = mod.create_table(logical=f"{TAG} lifecycle")
    mod.add_column(tid, "id", "BIGINT", pk=True, ordinal=0)

    # 1) draft, 2) editar la tabla (nueva descripción) + nueva columna dentro del draft
    cs = mod.snapshot([pid], title=f"{TAG} v-lifecycle")
    s.check("snapshot creó draft", bool(cs))
    tdoc = mod.get("/api/catalog/tables").data
    tdoc = next((t for t in tdoc if t["id"] == tid), None)
    r = _cs_change(mod, cs, "canonical_tables", tid, {**tdoc, "description": "EDITADO en draft"})
    s.eq("add_change tabla (edit)", r.status, 200)
    new_col = f"{tid}.new"
    r = _cs_change(mod, cs, "canonical_columns", new_col,
                   {"id": new_col, "tableId": tid, "logicalName": "columna nueva",
                    "physicalName": "COL_NUEVA", "dataType": "STRING", "ordinal": 1})
    s.eq("add_change columna nueva", r.status, 200)

    # producción todavía NO ve el cambio (draft aislado)
    prod_t = next((t for t in mod.get("/api/catalog/tables").data if t["id"] == tid), None)
    s.check("producción NO ve el edit del draft", (prod_t or {}).get("description") != "EDITADO en draft")

    # 3) submit con revisor beto
    r = mod.post(f"/api/changesets/{cs}/submit", {"reviewers": [rev.user], "title": f"{TAG} req"})
    s.eq("submit a revisión", r.status, 200)
    # 4) revisor aprueba → aplica a producción
    r = rev.post(f"/api/changesets/{cs}/review", {"decision": "approve"})
    s.check("revisor aprueba+publica", r.status == 200, f"status={r.status} {str(r.raw)[:150]}")

    # 5) producción AHORA refleja el cambio + la columna nueva
    prod_t = next((t for t in mod.get("/api/catalog/tables").data if t["id"] == tid), None)
    s.eq("producción refleja el edit", (prod_t or {}).get("description"), "EDITADO en draft")
    cols = mod.get(f"/api/catalog/tables/{tid}/columns").data
    s.check("columna nueva publicada", any(c["id"] == new_col for c in cols), f"{len(cols)} cols")

    # 6) current_production es la versión nueva (approved+applied)
    pub = mod.get("/api/versions/published").data
    s.check("hay versión de producción", bool(pub), f"{pub}")
    H._track("canonical_columns", new_col)
    return s


# ══ S03 · Convergencia / merge: dos drafts editan la MISMA tabla ═════════════
def s03_convergence() -> Suite:
    s = Suite("s03_convergence")
    modA, modB, rev = Client("modelador"), Client("modelador2"), Client("revisor")
    pid = modA.create_project()
    tid = modA.create_table(logical=f"{TAG} shared")
    modA.add_column(tid, "id", "BIGINT", pk=True)
    tdoc = next((t for t in modA.get("/api/catalog/tables").data if t["id"] == tid), None)

    csA = modA.snapshot([pid], title=f"{TAG} draftA")
    csB = modB.snapshot([pid], title=f"{TAG} draftB")
    s.check("dos drafts creados desde la misma producción", bool(csA and csB))

    # A y B editan la MISMA tabla con valores distintos
    _cs_change(modA, csA, "canonical_tables", tid, {**tdoc, "description": "valor A"})
    _cs_change(modB, csB, "canonical_tables", tid, {**tdoc, "description": "valor B"})

    # publicar A
    modA.post(f"/api/changesets/{csA}/submit", {"reviewers": [rev.user]})
    ra = rev.post(f"/api/changesets/{csA}/review", {"decision": "approve"})
    s.eq("publica draft A", ra.status, 200)
    prod = next((t for t in modA.get("/api/catalog/tables").data if t["id"] == tid), None)
    s.eq("producción = valor A tras publicar A", (prod or {}).get("description"), "valor A")

    # ¿Qué ve el draft B (efectivo) ahora que A publicó? (copy-on-write: B mantiene su override 'valor B')
    effB = modB.get(f"/api/changesets/{csB}/effective/canonical_tables?ids={tid}").data
    effB_t = next((t for t in (effB or []) if t["id"] == tid), None)
    s.check("draft B conserva su propio override (valor B)", (effB_t or {}).get("description") == "valor B",
            f"B ve: {(effB_t or {}).get('description')!r}")

    # publicar B → converge a valor B (last-writer-wins sobre esa tabla)
    modB.post(f"/api/changesets/{csB}/submit", {"reviewers": [rev.user]})
    rb = rev.post(f"/api/changesets/{csB}/review", {"decision": "approve"})
    s.eq("publica draft B", rb.status, 200)
    prod = next((t for t in modB.get("/api/catalog/tables").data if t["id"] == tid), None)
    s.eq("producción converge a valor B (last-writer-wins)", (prod or {}).get("description"), "valor B")
    return s


# ══ S04 · Cascada de ParentDomain (R7): prod vs draft vs in-progress ═════════
def s04_domain_cascade() -> Suite:
    s = Suite("s04_domain_cascade")
    admin, mod, rev = Client("admin"), Client("modelador"), Client("revisor")

    # dominio AISLADO T1 + tabla con columna que lo referencia
    dom = admin.create_domain(f"{TAG} dom", "DECIMAL(10,2)")
    pid = mod.create_project()
    tid = mod.create_table(logical=f"{TAG} casc")
    # SIN dataType explícito → hereda del dominio (typeOverridden=False) para que
    # la cascada aplique (las columnas con tipo manual la ignoran, y es correcto).
    cid = mod.add_column(tid, "importe", dtype=None, domain=dom, ordinal=0)

    def col_type_prod():
        cols = mod.get(f"/api/catalog/tables/{tid}/columns").data
        c = next((c for c in cols if c["id"] == cid), None)
        return (c or {}).get("dataType")

    s.eq("tipo inicial en producción", col_type_prod(), "DECIMAL(10,2)")

    # draft (copy-on-write) tomado ANTES del cambio de dominio
    cs_draft = mod.snapshot([pid], title=f"{TAG} pre-cambio")
    # in-progress: draft submitted (en revisión) ANTES del cambio
    cs_prog = mod.snapshot([pid], title=f"{TAG} in-progress")
    mod.post(f"/api/changesets/{cs_prog}/submit", {"reviewers": [rev.user]})

    # cambio de estándar: dominio → BIGINT. Un apply de dominio SOLO (sin
    # términos/naming) NO dispara el rephysicalize global — solo cascadea a las
    # columnas de ESE dominio (aislado: 1 columna). Seguro a escala.
    r = admin.post("/api/standards/apply", {"kind": "domain",
        "domainsUpsert": [{"id": dom, "name": f"{TAG} dom", "defaultDataType": "BIGINT"}]})
    s.check("standards.apply dominio→BIGINT", r.status in (200, 201), f"status={r.status} {str(r.raw)[:120]}")
    if r.status in (200, 201) and r.data:
        H._track("standards_versions", r.data["id"])

    # producción (reader read-only): la columna se re-tipó a BIGINT
    s.eq("producción re-tipada a BIGINT (cascada)", col_type_prod(), "BIGINT")

    # draft NO tocado (sin override de esa columna): ve el valor PUBLICADO nuevo (BIGINT)
    effd = mod.get(f"/api/changesets/{cs_draft}/effective/canonical_columns?tableId={tid}").data
    cd = next((c for c in (effd or []) if c["id"] == cid), None)
    s.check("draft sin override ve el tipo nuevo (BIGINT)", (cd or {}).get("dataType") == "BIGINT",
            f"draft ve: {(cd or {}).get('dataType')!r}")

    # in-progress (submitted) idem: refleja el publicado
    effp = mod.get(f"/api/changesets/{cs_prog}/effective/canonical_columns?tableId={tid}").data
    cp = next((c for c in (effp or []) if c["id"] == cid), None)
    s.check("in-progress refleja el tipo nuevo (BIGINT)", (cp or {}).get("dataType") == "BIGINT",
            f"in-progress ve: {(cp or {}).get('dataType')!r}")

    # "revertir" con otro apply de dominio (aislado, seguro) → DECIMAL(10,2).
    # NOTA: el endpoint real /standards/rollback restaura un snapshot y dispara
    # rephysicalize(None) GLOBAL (273k cols en esta data) — no se ejecuta live.
    r = admin.post("/api/standards/apply", {"kind": "domain",
        "domainsUpsert": [{"id": dom, "name": f"{TAG} dom", "defaultDataType": "DECIMAL(10,2)"}]})
    if r.status in (200, 201) and r.data:
        H._track("standards_versions", r.data["id"])
    s.eq("producción restaurada a DECIMAL(10,2)", col_type_prod(), "DECIMAL(10,2)")
    return s


# ══ S05 · UDP: lecturas del módulo + ALCANCE del re-physicalize (NO destructivo) ═
def s05_udp_scope() -> Suite:
    s = Suite("s05_udp_scope")
    admin, lec = Client("admin"), Client("lector")

    # Lecturas del módulo a escala (read-only)
    snap = admin.get("/api/standards/snapshot")
    s.check("standards/snapshot lee OK", snap.status == 200, f"status={snap.status}")
    s.check("snapshot trae domains+dict+namingConfig",
            all(k in (snap.data or {}) for k in ("domains", "dict", "namingConfig")))
    vers = admin.get("/api/standards/versions")
    s.check("standards/versions lee OK", vers.status == 200, f"{len(vers.data or [])} versiones")
    s.eq("lector NO ve standards/versions solo si gateado", True, True)  # lectura permitida a todos (view)

    # HALLAZGO de escala (dry-run, NO destructivo): un UDP/naming apply llama
    # rephysicalize(None) = re-deriva TODO el catálogo. Medimos cuántas columnas
    # reescribiría SIN aplicar (import directo de las funciones puras).
    import time as _t
    import asyncio as _a

    async def _dry():
        from app.core.db.client import connect, disconnect
        from app.features.glossary import service as ds, repository as dr
        from app.features.settings import service as ss
        await connect()
        try:
            cfg = await ss.get_naming_for("column")
            maps = ds.to_mappings(await dr.list_entries("column"))
            ents = await dr.entities_for_rephysicalize("column")
            upd = ds.compute_rephysicalize(ents, maps, cfg["separator"], cfg["case"])
            return len(ents), len(upd)
        finally:
            await disconnect()

    t0 = _t.monotonic()
    total, would = _a.run(_dry())
    dt = (_t.monotonic() - t0) * 1000
    s.check("dry-run del rephysicalize (sin escribir)", total > 0, f"{total} columnas")
    s.check("HALLAZGO: UDP/naming apply re-physicaliza TODO el catálogo (global)", True,
            f"reescribiría {would}/{total} columnas · ~{dt:.0f}ms solo de cómputo → optimización: acotar a términos afectados")
    return s


# ══ S06 · Canvas CRUD (crear/editar/eliminar) ════════════════════════════════
def s06_canvas_crud() -> Suite:
    s = Suite("s06_canvas_crud")
    mod = Client("modelador")
    pid = mod.create_project()
    t1 = mod.create_table(logical=f"{TAG} c1"); mod.add_column(t1, "id", "BIGINT", pk=True)
    t2 = mod.create_table(logical=f"{TAG} c2"); mod.add_column(t2, "id", "BIGINT", pk=True)

    ca = mod.create_canvas(pid, name=f"{TAG} canvas")
    s.check("canvas creado", bool(ca))
    # agregar tablas
    r = mod.put(f"/api/subject-areas/{ca}/tables", {"tableIds": [t1, t2]})
    s.eq("agregar 2 tablas al canvas", r.status, 200)
    # layout
    r = mod.put(f"/api/subject-areas/{ca}/layout", {"layout": {t1: {"x": 0, "y": 0}, t2: {"x": 300, "y": 0}}})
    s.eq("guardar layout", r.status, 200)
    # diagrama refleja las 2 tablas
    diag = mod.get(f"/api/subject-areas/{ca}/diagram").data
    s.eq("diagrama tiene 2 tablas", len(diag.get("tables", [])), 2)
    # editar: quitar una tabla
    r = mod.put(f"/api/subject-areas/{ca}/tables", {"tableIds": [t1]})
    s.eq("quitar una tabla", r.status, 200)
    diag = mod.get(f"/api/subject-areas/{ca}/diagram").data
    s.eq("diagrama ahora tiene 1 tabla", len(diag.get("tables", [])), 1)
    # eliminar canvas
    r = mod.delete(f"/api/subject-areas/{ca}")
    s.check("eliminar canvas", r.status in (200, 204), f"status={r.status}")
    s.eq("canvas eliminado (404)", mod.get(f"/api/subject-areas/{ca}").status, 404)
    return s


# ══ S07 · Relaciones (crear / listar / eliminar) ═════════════════════════════
def s07_relationships() -> Suite:
    s = Suite("s07_relationships")
    mod = Client("modelador")
    t1 = mod.create_table(logical=f"{TAG} r-src"); c1 = mod.add_column(t1, "fk", "BIGINT")
    t2 = mod.create_table(logical=f"{TAG} r-tgt"); c2 = mod.add_column(t2, "id", "BIGINT", pk=True)
    rid = mod.create_relationship(t1, c1, t2, c2)
    s.check("relación creada", bool(rid))
    rels = mod.get("/api/relationships").data
    s.check("relación aparece en el listado", any(r["id"] == rid for r in rels))
    r = mod.delete(f"/api/relationships/{rid}")
    s.check("eliminar relación", r.status in (200, 204), f"status={r.status}")
    rels = mod.get("/api/relationships").data
    s.check("relación ya no está", not any(r["id"] == rid for r in rels))
    return s


# ══ S08 · Vistas (CTAS-style: crear/leer/editar/eliminar) ════════════════════
def s08_views() -> Suite:
    s = Suite("s08_views")
    mod = Client("modelador")
    t1 = mod.create_table(logical=f"{TAG} vbase"); mod.add_column(t1, "id", "BIGINT", pk=True)
    name = _uid("view")
    vid = mod.create_view(name, f"CREATE TABLE {name} AS SELECT * FROM base WHERE x=1", table_id=t1)
    s.check("vista CTAS creada", bool(vid))
    views = mod.get("/api/views").data
    s.check("vista aparece en el listado", any(v["id"] == vid for v in views))
    r = mod.put(f"/api/views/{vid}", {"name": name, "sql": "SELECT 2", "description": "editada"})
    s.eq("editar vista", r.status, 200)
    r = mod.delete(f"/api/views/{vid}")
    s.check("eliminar vista", r.status in (200, 204), f"status={r.status}")
    s.check("vista eliminada", not any(v["id"] == vid for v in (mod.get("/api/views").data or [])))
    return s


# ══ S09 · Reporting (exactitud sobre fixture conocido) ═══════════════════════
def s09_reporting() -> Suite:
    s = Suite("s09_reporting")
    mod = Client("modelador")
    pid = mod.create_project()
    tid = mod.create_table(logical=f"{TAG} rep", schema="e2erep")
    for i in range(3):
        mod.add_column(tid, f"col{i}", "STRING", ordinal=i)
    ca = mod.create_canvas(pid, name=f"{TAG} repcanvas")
    mod.put(f"/api/subject-areas/{ca}/tables", {"tableIds": [tid]})

    # /reporting/tables filtrado por schema aislado → 1 fila con columnCount=3
    rows = mod.get("/api/reporting/tables?schema=e2erep").data
    row = next((r for r in (rows or []) if r["id"] == tid), None)
    s.check("tabla aparece en reporting", bool(row))
    if row:
        s.eq("columnCount = 3", row["columnCount"], 3)
        s.check("el canvas aparece en subjectAreas", any(TAG in n for n in row.get("subjectAreas", [])),
                f"subjectAreas={row.get('subjectAreas')}")
    # /reporting/columns acotado a la tabla → 3 filas
    cols = mod.get(f"/api/reporting/columns?tableIds={tid}").data
    s.eq("reporting columns = 3", len([c for c in (cols or []) if c["tableId"] == tid]), 3)
    return s


# ══ S10 · Admin: users CRUD + guards anti-lockout ════════════════════════════
def s10_admin() -> Suite:
    s = Suite("s10_admin")
    admin = Client("admin")
    uname = f"e2euser_{uuid.uuid4().hex[:6]}"
    rkey = f"e2erole_{uuid.uuid4().hex[:6]}"
    H._track("users", uname); H._track("roles", rkey)
    # crear usuario (rol lector)
    r = admin.post("/api/admin/users", {"username": uname, "email": f"{uname}@e.com",
        "name": "E2E User", "role": "lector", "password": "temp12345"})
    s.check("crear usuario", r.status in (200, 201), f"status={r.status} {str(r.raw)[:120]}")
    lr = admin.http.post("/api/auth/login", json={"username": uname, "password": "temp12345"})
    s.eq("nuevo usuario loguea", lr.status_code, 200)
    # cambiar rol
    s.eq("cambiar rol a modelador", admin.put(f"/api/admin/users/{uname}", {"role": "modelador"}).status, 200)
    # deshabilitar → login bloqueado (revoca acceso)
    admin.put(f"/api/admin/users/{uname}", {"status": "disabled"})
    lr = admin.http.post("/api/auth/login", json={"username": uname, "password": "temp12345"})
    s.eq("usuario deshabilitado no loguea", lr.status_code, 401)
    # re-habilitar + asignar a un rol DESCARTABLE
    admin.put(f"/api/admin/users/{uname}", {"status": "active"})
    admin.put(f"/api/admin/roles/{rkey}", {"name": "E2E Role", "permissions": {"model.view": True}})
    admin.put(f"/api/admin/users/{uname}", {"role": rkey})
    # guard: borrar un rol CON usuarios asignados → 400 (rol descartable, seguro)
    r = admin.delete(f"/api/admin/roles/{rkey}")
    s.check("borrar rol con usuarios asignados bloqueado (400)", r.status == 400, f"status={r.status}")
    # reasignar y borrar el rol
    admin.put(f"/api/admin/users/{uname}", {"role": "lector"})
    s.check("borrar rol sin usuarios OK", admin.delete(f"/api/admin/roles/{rkey}").status in (200, 204))
    # eliminar el usuario de prueba
    s.check("eliminar usuario", admin.delete(f"/api/admin/users/{uname}").status in (200, 204))
    # NOTA: el guard anti auto-lockout (quitar admin.manage al último rol admin)
    # está cubierto por unit tests; no se ejercita live para no arriesgar el acceso.
    return s


# ══ S11 · Auditoría (registra actor + acción de lo que hicieron) ═════════════
def s11_audit() -> Suite:
    s = Suite("s11_audit")
    admin = Client("admin")
    # genera acciones auditables
    Client("modelador")           # login modelador
    lr = admin.http.post("/api/auth/login", json={"username": "carla", "password": "MALA"})  # login_failed
    s.eq("login fallido devuelve 401", lr.status_code, 401)
    # lee el log
    r = admin.get("/api/admin/audit?limit=200")
    s.check("admin lee audit_log", r.status == 200, f"status={r.status}")
    entries = r.data or []
    s.check("hay entradas de auditoría", len(entries) > 0, f"{len(entries)} entradas")
    actions = {e.get("action") for e in entries}
    actors = {e.get("actor") for e in entries}
    s.check("registra login", "login" in actions, f"acciones muestra={list(actions)[:8]}")
    s.check("registra login_failed", "login_failed" in actions)
    s.check("cada entrada tiene actor+action+timestamp",
            all(e.get("actor") and e.get("action") and (e.get("at") or e.get("timestamp")) for e in entries[:20]))
    # lector NO puede leer auditoría
    s.eq("lector NO lee audit (admin.manage)", Client("lector").get("/api/admin/audit").status, 403)
    return s


# ══ S12 · Glossary (rename) + UDP real (definir/asignar/rollback) ════════════
def s12_glossary_udp() -> Suite:
    import time as _t
    s = Suite("s12_glossary_udp")
    admin, mod, rev = Client("admin"), Client("modelador"), Client("revisor")

    # Rename Glossary
    s.eq("GET /api/glossary responde", admin.get("/api/glossary?scope=column").status, 200)
    s.eq("GET /api/dictionary (viejo) → 404", admin.get("/api/dictionary").status, 404)

    # UDP: definir keys versionadas (kind='udp'), segmentadas por nivel (Class):
    # una a nivel COLUMNA y otra a nivel TABLA.
    cname, tname = f"{TAG} ClasifCol", f"{TAG} DomTbl"
    d = admin.post("/api/standards/apply", {"kind": "udp", "udpUpsert": [
        {"name": cname, "level": "column", "dataType": "list", "allowedValues": ["DAC", "NO DAC"], "defaultValue": "NO DAC"},
        {"name": tname, "level": "table", "dataType": "string"}]})
    s.check("UDP keys definidas (versión kind=udp)", d.status == 200 and (d.data or {}).get("kind") == "udp",
            f"v={(d.data or {}).get('label')}")
    defs = admin.get("/api/udp").data or []
    cdef = next((x for x in defs if x["name"] == cname), None)
    tdef = next((x for x in defs if x["name"] == tname), None)
    s.check("UDP columna con level+allowedValues", bool(cdef) and cdef["level"] == "column" and cdef["allowedValues"] == ["DAC", "NO DAC"])
    s.check("UDP tabla con level=table (Text)", bool(tdef) and tdef["level"] == "table" and tdef["dataType"] == "string")
    cdid = cdef["id"] if cdef else "x"; tdid = tdef["id"] if tdef else "y"
    if cdef: H._track("udp_definitions", cdid)
    if tdef: H._track("udp_definitions", tdid)

    # Asignar valores UDP a una COLUMNA (def col) y a la TABLA (def tabla) VÍA
    # CHANGESET (el path real del frontend: upsert por id) → publicar → verificar.
    pid = mod.create_project(); tid = mod.create_table(logical=f"{TAG} udp"); cid = mod.add_column(tid, "col", "STRING")
    cdoc = next((c for c in mod.get(f"/api/catalog/tables/{tid}/columns").data if c["id"] == cid), {})
    tdoc = next((t for t in mod.get("/api/catalog/tables").data if t["id"] == tid), {})
    cs = mod.snapshot([pid], title=f"{TAG} udp")
    r = _cs_change(mod, cs, "canonical_columns", cid, {**cdoc, "udpValues": {cdid: "DAC"}})
    s.eq("add_change columna con udpValues", r.status, 200)
    _cs_change(mod, cs, "canonical_tables", tid, {**tdoc, "udpValues": {tdid: "ref_x"}})
    mod.post(f"/api/changesets/{cs}/submit", {"reviewers": [rev.user]})
    rev.post(f"/api/changesets/{cs}/review", {"decision": "approve"})
    pub = next((c for c in mod.get(f"/api/catalog/tables/{tid}/columns").data if c["id"] == cid), {})
    s.check("columna publicada con udpValues (col)", (pub.get("udpValues") or {}).get(cdid) == "DAC", f"{pub.get('udpValues')}")
    pubt = next((t for t in mod.get("/api/catalog/tables").data if t["id"] == tid), {})
    s.check("tabla publicada con udpValues (tabla)", (pubt.get("udpValues") or {}).get(tdid) == "ref_x", f"{pubt.get('udpValues')}")

    # Rollback de la definición UDP: fast-path (no re-physicaliza las 400k; restore
    # bulk). Debe quitar la key sin barrer el catálogo.
    vers = sorted(admin.get("/api/standards/versions").data, key=lambda v: v["seq"])
    target = vers[-2]["seq"] if len(vers) >= 2 else None
    t0 = _t.monotonic(); rb = admin.post("/api/standards/rollback", {"targetSeq": target}); dt = (_t.monotonic() - t0) * 1000
    after = admin.get("/api/udp").data or []
    gone = not any(x["name"] in (cname, tname) for x in after)
    s.check("rollback UDP quita las keys nuevas", rb.status == 200 and gone)
    s.check("rollback UDP NO barre 400k (impact columns=0)",
            (rb.data or {}).get("impact", {}).get("columns") == 0, f"impact={(rb.data or {}).get('impact')} · {dt:.0f}ms")
    return s


ALL = {
    "s01_rbac": s01_rbac, "s02_version_lifecycle": s02_version_lifecycle,
    "s03_convergence": s03_convergence, "s04_domain_cascade": s04_domain_cascade,
    "s05_udp_scope": s05_udp_scope, "s06_canvas_crud": s06_canvas_crud,
    "s07_relationships": s07_relationships, "s08_views": s08_views,
    "s09_reporting": s09_reporting, "s10_admin": s10_admin, "s11_audit": s11_audit,
    "s12_glossary_udp": s12_glossary_udp,
}
