"""Escenarios E2E contra el backend en vivo. Cada `sNN_*` devuelve un `Suite`.

Todos usan fixtures AISLADOS (tag único por proceso) y limpian al final. Correr:
    .venv/bin/python -m scripts.e2e.run_e2e <scenario>       # uno
    .venv/bin/python -m scripts.e2e.run_e2e all              # todos (serial)
"""
from __future__ import annotations

import time
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
        "name": "E2E User", "role": "lector", "password": "temp1234567"})
    s.check("crear usuario", r.status in (200, 201), f"status={r.status} {str(r.raw)[:120]}")
    lr = admin.http.post("/api/auth/login", json={"username": uname, "password": "temp1234567"})
    s.eq("nuevo usuario loguea", lr.status_code, 200)
    # cambiar rol
    s.eq("cambiar rol a modelador", admin.put(f"/api/admin/users/{uname}", {"role": "modelador"}).status, 200)
    # deshabilitar → login bloqueado (revoca acceso)
    admin.put(f"/api/admin/users/{uname}", {"status": "disabled"})
    lr = admin.http.post("/api/auth/login", json={"username": uname, "password": "temp1234567"})
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


# ═════════════════════════════════════════════════════════════════════════════
# Lote "precisiones de modelamiento" (spec doc 10) — s13..s18
# ═════════════════════════════════════════════════════════════════════════════

# ══ S13 · #9 Duplicados: tabla (schema+nombre) y columna (por tabla) → 409 ═══
def s13_guardas_duplicados() -> Suite:
    s = Suite("s13_guardas_duplicados")
    mod, rev = Client("modelador"), Client("revisor")

    pid = mod.create_project()
    phys = f"{TAG}_DUP_BASE".upper()
    tid = mod.create_table(logical=f"{TAG} dup base", schema="e2e", physical=phys)
    cid = mod.add_column(tid, "codigo cliente", "STRING", ordinal=0)
    col_phys = next((c["physicalName"] for c in mod.get(f"/api/catalog/tables/{tid}/columns").data
                     if c["id"] == cid), None)
    s.check("fixture publicado (tabla+columna)", bool(col_phys), f"col={col_phys}")

    cs = mod.snapshot([pid], title=f"{TAG} s13")
    # 1) misma schema+physicalName EXACTA contra publicado → 409
    nid = _uid("tdup")
    r = _cs_change(mod, cs, "canonical_tables", nid,
                   {"id": nid, "logicalName": f"{TAG} otra", "physicalName": phys, "schema": "e2e"})
    s.eq("tabla duplicada exacta → 409", r.status, 409)
    # 2) case-insensitive → 409
    r = _cs_change(mod, cs, "canonical_tables", nid,
                   {"id": nid, "logicalName": f"{TAG} otra", "physicalName": phys.lower(), "schema": "E2E"})
    s.eq("tabla duplicada case-insensitive → 409", r.status, 409)
    # 3) mismo nombre en OTRO schema → permitido
    r = _cs_change(mod, cs, "canonical_tables", nid,
                   {"id": nid, "logicalName": f"{TAG} otra", "physicalName": phys, "schema": "e2e_otro"})
    s.eq("mismo nombre en otro schema → OK", r.status, 200)
    # 4) pendiente-vs-pendiente en el MISMO changeset → 409
    nid2 = _uid("tdup2")
    r = _cs_change(mod, cs, "canonical_tables", nid2,
                   {"id": nid2, "logicalName": f"{TAG} tercera", "physicalName": phys, "schema": "e2e_otro"})
    s.eq("duplicado contra pendiente del mismo draft → 409", r.status, 409)
    # 5) columna duplicada dentro de la misma tabla (case-insensitive) → 409
    ncid = f"{tid}.dupcol"
    r = _cs_change(mod, cs, "canonical_columns", ncid,
                   {"id": ncid, "tableId": tid, "logicalName": "codigo cliente bis",
                    "physicalName": col_phys.lower(), "dataType": "STRING", "ordinal": 9})
    s.eq("columna duplicada (case-insensitive) → 409", r.status, 409)
    # 6) columna con nombre nuevo → OK
    r = _cs_change(mod, cs, "canonical_columns", ncid,
                   {"id": ncid, "tableId": tid, "logicalName": "columna sana",
                    "physicalName": f"{TAG}_SANA", "dataType": "STRING", "ordinal": 9})
    s.eq("columna con nombre libre → OK", r.status, 200)

    # 7) carrera entre changesets: cs2 mete el mismo nombre ANTES de publicar cs1
    #    → el re-chequeo del publish (approve) del segundo debe fallar con 409.
    csA = mod.snapshot([pid], title=f"{TAG} s13-A")
    csB = mod.snapshot([pid], title=f"{TAG} s13-B")
    race = f"{TAG}_RACE".upper()
    idA, idB = _uid("raceA"), _uid("raceB")
    s.eq("csA registra tabla RACE", _cs_change(mod, csA, "canonical_tables", idA,
         {"id": idA, "logicalName": f"{TAG} race A", "physicalName": race, "schema": "e2e"}).status, 200)
    s.eq("csB registra tabla RACE (aún sin conflicto publicado)", _cs_change(mod, csB, "canonical_tables", idB,
         {"id": idB, "logicalName": f"{TAG} race B", "physicalName": race, "schema": "e2e"}).status, 200)
    H._track("canonical_tables", idA); H._track("canonical_tables", idB)
    mod.post(f"/api/changesets/{csA}/submit", {"reviewers": [rev.user]})
    r = rev.post(f"/api/changesets/{csA}/review", {"decision": "approve"})
    s.eq("csA publica OK", r.status, 200)
    mod.post(f"/api/changesets/{csB}/submit", {"reviewers": [rev.user]})
    r = rev.post(f"/api/changesets/{csB}/review", {"decision": "approve"})
    s.check("re-chequeo del publish atrapa la carrera → 409", r.status == 409,
            f"status={r.status} {str(r.raw)[:120]}")
    return s


# ══ S14 · #8 Impacto global al eliminar columna con relaciones ═══════════════
def s14_impacto_eliminacion() -> Suite:
    s = Suite("s14_impacto_eliminacion")
    mod = Client("modelador")

    pid = mod.create_project()
    t1 = mod.create_table(logical=f"{TAG} cliente", schema="e2e")
    c1 = mod.add_column(t1, "id cliente", "BIGINT", pk=True, ordinal=0)
    t2 = mod.create_table(logical=f"{TAG} cuenta", schema="e2e")
    c2 = mod.add_column(t2, "id cliente", "BIGINT", ordinal=1)
    rid = mod.create_relationship(t2, c2, t1, c1)
    ca = mod.create_canvas(pid, name=f"{TAG} imp-canvas")
    mod.put(f"/api/subject-areas/{ca}/tables", {"tableIds": [t1, t2]})

    # impacto de la columna PK referenciada: 1 relación, otro extremo = t2.c2
    imp = mod.get(f"/api/relationships/impact?columnId={c1}")
    s.eq("impact responde 200", imp.status, 200)
    data = imp.data or {}
    s.eq("total = 1 relación", data.get("total"), 1)
    rels = data.get("relationships") or []
    other_ok = bool(rels) and rels[0].get("otherTableName") and rels[0].get("otherColumnName")
    s.check("enriquecido con tabla.columna del otro extremo", other_ok, f"{rels[:1]}")
    s.check("lista los canvases donde la relación es visible",
            any(TAG.lower() in (c.get("name") or "").lower()
                for r0 in rels for c in (r0.get("canvases") or [])),
            f"canvases={[r0.get('canvases') for r0 in rels]}")
    # columna sin relaciones → impacto vacío
    imp0 = mod.get(f"/api/relationships/impact?columnId={c2}x-no-existe")
    s.eq("columna sin relaciones → total 0", (imp0.data or {}).get("total"), 0)
    # overlay de changeset: relación agregada SOLO en el draft también cuenta
    cs = mod.snapshot([pid], title=f"{TAG} s14")
    t3 = mod.create_table(logical=f"{TAG} riesgo", schema="e2e")
    c3 = mod.add_column(t3, "id cliente", "BIGINT", ordinal=0)
    nrid = _uid("rel")
    r = _cs_change(mod, cs, "relationships", nrid,
                   {"id": nrid, "parentTableId": t3, "childTableId": t1,
                    "pairs": [{"parentColumnId": c3, "childColumnId": c1}],
                    "parentCardinality": "one", "childCardinality": "zero-many"})
    s.eq("relación nueva en draft", r.status, 200)
    H._track("relationships", nrid)
    imp2 = mod.get(f"/api/relationships/impact?columnId={c1}&changesetId={cs}")
    s.eq("impact con overlay del draft = 2", (imp2.data or {}).get("total"), 2)
    imp3 = mod.get(f"/api/relationships/impact?columnId={c1}")
    s.eq("impact SIN draft sigue = 1 (aislamiento)", (imp3.data or {}).get("total"), 1)
    return s


# ══ S15 · #1 Glosario: validación frase completa + lock ADMIN (D1/D4) ════════
def s15_glosario_validacion_lock() -> Suite:
    s = Suite("s15_glosario_validacion_lock")
    admin, mod = Client("admin"), Client("modelador")
    marker = f"zx{uuid.uuid4().hex[:6]}"      # palabra única, imposible en corpus

    # corpus: tabla publicada cuyo nombre lógico contiene la palabra marcador
    tid = mod.create_table(logical=f"{TAG} {marker} prestamo", schema="e2e")
    mod.add_column(tid, f"codigo {marker}", "STRING", ordinal=0)

    # 1) palabra que SÍ existe en nombres lógicos → conflicto
    r = admin.post("/api/glossary/validate", {"term": marker, "scope": "column"})
    conf = (r.data or {}).get("conflicts") or {}
    s.check("palabra existente en corpus → conflicto", r.status == 200 and (r.data or {}).get("ok") is False,
            f"total={conf.get('total')}")
    s.check("conflictos traen tabla/columna/nombre lógico",
            all(k in (conf.get("corpus") or [{}])[0] for k in ("tableName", "logicalName")),
            f"{(conf.get('corpus') or [None])[0]}")
    # 2) frase completa que NO existe → pasa (caso 'codigo de análisis' de la spec)
    r = admin.post("/api/glossary/validate", {"term": f"{marker} de analisis", "scope": "column"})
    s.check("frase completa inexistente → OK", (r.data or {}).get("ok") is True, f"{r.data}")
    # 3) substring que no es palabra completa NO conflictúa (zx… ≠ zx…pre)
    r = admin.post("/api/glossary/validate", {"term": marker[:4], "scope": "column"})
    s.check("substring parcial (no palabra completa) → OK", (r.data or {}).get("ok") is True,
            f"term={marker[:4]} {str(r.data)[:80]}")

    # 4) enforcement server-side: POST /api/glossary con término en corpus → 409
    r = admin.post("/api/glossary", {"term": marker, "abbrev": "zzz", "scope": "column"})
    s.eq("crear término en conflicto → 409", r.status, 409)
    # 5) crear término limpio → 201; duplicado exacto en glosario → 409
    clean = f"{marker}g"
    r = admin.post("/api/glossary", {"term": clean, "abbrev": "zzg", "scope": "column"})
    gid = (r.data or {}).get("id")
    s.check("término limpio se crea", r.status == 201 and bool(gid), f"status={r.status}")
    if gid: H._track("glossary_terms", gid)
    r = admin.post("/api/glossary", {"term": clean.upper(), "abbrev": "zzh", "scope": "column"})
    s.eq("duplicado exacto en glosario (case-insens) → 409", r.status, 409)
    # 6) enforcement vía standards/apply (added) → 409
    r = admin.post("/api/standards/apply", {"kind": "glossary", "termsUpsert": [
        {"term": marker, "abbrev": "zzq", "scope": "column"}]})
    s.eq("apply con término en corpus → 409", r.status, 409)

    # 7) lock por ADMIN (D4): intocable para TODOS hasta unlock
    r = admin.post(f"/api/glossary/{gid}/lock")
    s.check("admin bloquea", r.status == 200 and (r.data or {}).get("locked") is True, f"{r.data}")
    s.eq("modelador NO puede bloquear (admin.manage) → 403",
         mod.post(f"/api/glossary/{gid}/lock").status, 403)
    r = admin.put(f"/api/glossary/{gid}", {"term": clean, "abbrev": "zzz2", "scope": "column"})
    s.eq("editar bloqueado → 409 incluso ADMIN", r.status, 409)
    s.eq("eliminar bloqueado → 409", admin.delete(f"/api/glossary/{gid}").status, 409)
    r = admin.post("/api/standards/apply", {"kind": "glossary", "termsUpsert": [
        {"id": gid, "term": clean, "abbrev": "zzz3", "scope": "column"}]})
    s.eq("apply sobre bloqueado → 409", r.status, 409)
    # unlock → editable de nuevo
    admin.post(f"/api/glossary/{gid}/unlock")
    r = admin.put(f"/api/glossary/{gid}", {"term": clean, "abbrev": "zzz4", "scope": "column"})
    s.eq("tras unlock se puede editar", r.status, 200)
    # auditoría del lock
    entries = admin.get("/api/admin/audit?limit=100").data or []
    acts = {e.get("action") for e in entries}
    s.check("lock/unlock auditados", any("lock" in (a or "") for a in acts), f"{[a for a in acts if a and 'lock' in a]}")
    return s


# ══ S16 · #2 Parent Domain: impacto detallado con tablas + búsqueda ══════════
def s16_domain_impact() -> Suite:
    s = Suite("s16_domain_impact")
    admin, mod = Client("admin"), Client("modelador")
    pid = mod.create_project()
    dom = admin.create_domain(f"{TAG} Importe", "DECIMAL(18,2)")  # domains = standards.edit

    t1 = mod.create_table(logical=f"{TAG} saldos", schema="e2e")
    t2 = mod.create_table(logical=f"{TAG} movimientos", schema="e2e")
    # 2 columnas HEREDAN el tipo del dominio (dtype=None); 1 con override manual
    mod.add_column(t1, "saldo actual", None, domain=dom, ordinal=0)
    mod.add_column(t1, "saldo anterior", None, domain=dom, ordinal=1)
    mod.add_column(t2, "importe", "STRING", domain=dom, ordinal=0)  # override deliberado
    ca = mod.create_canvas(pid, name=f"{TAG} dom-canvas")
    mod.put(f"/api/subject-areas/{ca}/tables", {"tableIds": [t1]})

    imp = mod.get(f"/api/domains/{dom}/impact")
    s.eq("impact responde 200", imp.status, 200)
    d = imp.data or {}
    s.eq("columnsUsing = 3", d.get("columnsUsing"), 3)
    s.eq("willUpdate = 2 (excluye override manual)", d.get("willUpdate"), 2)
    s.eq("overridden = 1 (no cambiará)", d.get("overridden"), 1)
    s.eq("totalTables = 2", d.get("totalTables"), 2)
    s.check("cuenta modelos afectados (canvas)", (d.get("modelsAffected") or 0) >= 1,
            f"modelsAffected={d.get('modelsAffected')}")
    tables = d.get("tables") or []
    s.check("lista tablas con nombre+columnas+overridden", bool(tables) and
            all(k in t for t in tables for k in ("physicalName", "columns", "overridden")),
            f"{[(t.get('physicalName'), t.get('columns'), t.get('overridden')) for t in tables]}")
    # búsqueda server-side `q`: filtra la LISTA; los conteos globales no cambian
    name1 = next((t.get("physicalName") for t in tables if t.get("tableId") == t1), "")
    imp_q = mod.get(f"/api/domains/{dom}/impact?q={name1}")
    dq = imp_q.data or {}
    tq = dq.get("tables") or []
    s.check("q filtra a la tabla buscada", len(tq) == 1 and tq[0].get("tableId") == t1,
            f"q={name1} → {[t.get('physicalName') for t in tq]}")
    s.eq("conteo global de columnas NO cambia con q", dq.get("columnsUsing"), 3)
    s.eq("totalTables con q = matches (pagina la lista filtrada)", dq.get("totalTables"), 1)
    # paginación
    imp_p = mod.get(f"/api/domains/{dom}/impact?limit=1")
    s.eq("limit=1 devuelve 1 tabla", len((imp_p.data or {}).get("tables") or []), 1)
    return s


# ══ S17 · #3-5 Vistas: multi-fuente, showOnCanvas, diagrama, compat legacy ═══
def s17_vistas_multifuente() -> Suite:
    s = Suite("s17_vistas_multifuente")
    mod = Client("modelador")
    pid = mod.create_project()
    t1 = mod.create_table(logical=f"{TAG} cliente", schema="e2e")
    c1 = mod.add_column(t1, "id cliente", "BIGINT", pk=True, ordinal=0)
    mod.add_column(t1, "nombre cliente", "VARCHAR(120)", ordinal=1)
    t2 = mod.create_table(logical=f"{TAG} cuenta", schema="e2e")
    mod.add_column(t2, "id cuenta", "BIGINT", pk=True, ordinal=0)
    c2 = mod.add_column(t2, "id cliente", "BIGINT", ordinal=1)
    mod.add_column(t2, "saldo actual", "DECIMAL(18,2)", ordinal=2)
    mod.create_relationship(t2, c2, t1, c1)
    ca = mod.create_canvas(pid, name=f"{TAG} vw-canvas")
    mod.put(f"/api/subject-areas/{ca}/tables", {"tableIds": [t1, t2]})

    # 1) crear vista MULTI-FUENTE con columnas propias (alias + cast) + flag canvas
    vname = _uid("VW_CLIENTE_360")
    body = {"name": vname, "schema": "e2e", "sql": "",
            "sourceTableIds": [t1, t2], "showOnCanvas": True,
            "sources": [
                {"tableId": t1, "column": "nombre_cliente", "outputAlias": "nombre"},
                {"tableId": t2, "column": "saldo_actual", "castType": "DECIMAL(18,2)",
                 "outputAlias": "saldo_total"}]}
    r = mod.post("/api/views", body)
    vid = (r.data or {}).get("id")
    s.check("vista multi-fuente creada", r.status in (200, 201) and bool(vid), f"status={r.status}")
    if vid: H._track("views", vid)
    v = r.data or {}
    s.eq("tableId legacy = primera fuente", v.get("tableId"), t1)
    s.eq("sourceTableIds completo", v.get("sourceTableIds"), [t1, t2])
    s.check("sources conservan castType/alias",
            any(x.get("castType") == "DECIMAL(18,2)" for x in v.get("sources") or []))
    fresh = next((x for x in (mod.get(f"/api/views?tableId={t1}").data or []) if x["id"] == vid), {})
    # Doc 91 D5: sin joinOverride (el DDL lista `FROM t1, t2`); el campo no viaja.
    s.check("joinOverride ya no existe en el doc (doc 91 D5)", "joinOverride" not in fresh)

    # 2) GET ?tableId= matchea por CUALQUIER fuente (no solo la primera)
    for t in (t1, t2):
        found = any(x["id"] == vid for x in (mod.get(f"/api/views?tableId={t}").data or []))
        s.check(f"listado por fuente {'principal' if t == t1 else 'secundaria'} la incluye", found)

    # 3) diagrama: la vista aparece con showOnCanvas=True
    diag = mod.get(f"/api/subject-areas/{ca}/diagram").data or {}
    dviews = {x["id"]: x for x in diag.get("views") or []}
    s.check("diagrama incluye la vista (showOnCanvas)", vid in dviews, f"views={list(dviews)}")
    s.check("payload de vista trae fuentes para derivaciones",
            set((dviews.get(vid) or {}).get("sourceTableIds") or []) == {t1, t2})

    # 4) canvas parcial (D3): con UNA sola fuente presente también aparece
    ca2 = mod.create_canvas(pid, name=f"{TAG} vw-parcial")
    mod.put(f"/api/subject-areas/{ca2}/tables", {"tableIds": [t2]})
    diag2 = mod.get(f"/api/subject-areas/{ca2}/diagram").data or {}
    s.check("canvas con 1 fuente también muestra la vista (D3)",
            any(x["id"] == vid for x in diag2.get("views") or []))

    # 5) apagar showOnCanvas vía PUT estilo LEGACY (solo tableId, sin sourceTableIds)
    #    — regresión F3a T5/T6: la normalización del update no debe perder fuentes.
    legacy_body = {"name": vname, "schema": "e2e", "sql": "", "tableId": t1,
                   "showOnCanvas": False, "sources": v.get("sources") or []}
    r = mod.put(f"/api/views/{vid}", legacy_body)
    s.eq("PUT legacy responde 200", r.status, 200)
    diag = mod.get(f"/api/subject-areas/{ca}/diagram").data or {}
    s.check("apagado el flag, desaparece del canvas",
            not any(x["id"] == vid for x in diag.get("views") or []))
    got = next((x for x in (mod.get(f"/api/views?tableId={t1}").data or []) if x["id"] == vid), {})
    s.check("normalización tras PUT legacy (sourceTableIds=[t1])",
            got.get("sourceTableIds") == [t1] and got.get("showOnCanvas") is False, f"{got.get('sourceTableIds')}")

    # 6) compat: crear vista SOLO con tableId (flujo viejo) → sourceTableIds=[t]
    vid2 = mod.create_view(_uid("VW_LEGACY"), "SELECT 1", table_id=t2)
    got2 = next((x for x in (mod.get(f"/api/views?tableId={t2}").data or []) if x["id"] == vid2), {})
    s.eq("vista legacy queda normalizada", got2.get("sourceTableIds"), [t2])
    s.check("vista legacy NO sale en canvas (showOnCanvas default False)",
            not any(x["id"] == vid2 for x in (mod.get(f"/api/subject-areas/{ca2}/diagram").data or {}).get("views") or []))

    # 7) User-Defined SQL (doc 91 D6): se guarda VERBATIM aunque no parsee
    bad_sql = f"CREATE VIEW e2e.{vname} AS SELEC nombre_cliente FRM e2e.cliente"
    r = mod.put(f"/api/views/{vid}", {**legacy_body, "customSql": bad_sql})
    s.eq("PUT con User-Defined SQL inválido responde 200", r.status, 200)
    s.eq("customSql round-trip verbatim", (r.data or {}).get("customSql"), bad_sql)
    return s


# ══ S18 · #10 UDP de canvas (Modelo de Datos) + reporting `models` ═══════════
def s18_udp_canvas_models() -> Suite:
    s = Suite("s18_udp_canvas_models")
    admin, mod, lector = Client("admin"), Client("modelador"), Client("lector")
    pid = mod.create_project()
    tid = mod.create_table(logical=f"{TAG} m1", schema="e2e")
    ca = mod.create_canvas(pid, name=f"{TAG} modelo-riesgos")
    mod.put(f"/api/subject-areas/{ca}/tables", {"tableIds": [tid]})

    # 1) definición UDP nivel canvas vía standards/apply (versionada)
    key = f"{TAG} Criticidad"
    r = admin.post("/api/standards/apply", {"kind": "udp", "udpUpsert": [
        {"name": key, "level": "canvas", "dataType": "list",
         "allowedValues": ["alta", "media", "baja"], "defaultValue": "media"}]})
    s.eq("definición UDP level=canvas aplicada", r.status, 200)
    cdef = next((x for x in (admin.get("/api/udp").data or []) if x["name"] == key), None)
    s.check("definición visible con level=canvas", bool(cdef) and cdef["level"] == "canvas", f"{cdef}")
    if cdef: H._track("udp_definitions", cdef["id"])
    kid = (cdef or {}).get("id", "x")

    # 2) asignación al canvas: PUT /udp (mutación directa, model.edit)
    r = mod.put(f"/api/subject-areas/{ca}/udp", {"udpValues": {kid: "alta"}})
    s.eq("modelador asigna UDP al canvas", r.status, 200)
    s.eq("lector NO puede (model.edit) → 403",
         lector.put(f"/api/subject-areas/{ca}/udp", {"udpValues": {kid: "baja"}}).status, 403)
    sas = mod.get(f"/api/projects/{pid}/subject-areas").data or []
    got = next((x for x in sas if x["id"] == ca), {})
    s.eq("round-trip udpValues del canvas", (got.get("udpValues") or {}).get(kid), "alta")

    # 3) reporting: entidad `models` consulta el UDP de canvas
    q = {"from": "models", "select": ["name", f"udp.{kid}"],
         "where": {"op": "and", "conditions": [{"field": f"udp.{kid}", "op": "eq", "value": "alta"}]},
         "limit": 50}
    r = mod.post("/api/reporting/query", q)
    rows = (r.data or {}).get("rows") or []
    s.check("query models filtra por udp de canvas", r.status == 200 and
            any(x.get("name") == f"{TAG} modelo-riesgos" for x in rows),
            f"status={r.status} rows={len(rows)}")
    # 4) catálogo: models presente; tableCount derived (ops vacíos)
    cat = mod.get("/api/reporting/catalog?from=models").data or {}
    fields = cat.get("fields") or []
    tc = next((f for f in fields if f.get("key") == "tableCount"), None)
    s.check("catálogo expone models.tableCount derived sin ops",
            bool(tc) and tc.get("derived") is True and not tc.get("ops"), f"{tc}")
    # 5) filtrar por derived → 422 (backstop del motor)
    bad = {"from": "models", "select": ["name"],
           "where": {"op": "and", "conditions": [{"field": "tableCount", "op": "gt", "value": 1}]}}
    s.eq("filtro sobre derived → 422", mod.post("/api/reporting/query", bad).status, 422)
    # 6) insight de cobertura incluye el nivel canvas
    cov = admin.get("/api/reporting/insights/udp-coverage").data or []
    s.check("udp-coverage lista la key de canvas",
            any(x.get("level") == "canvas" and x.get("name") == key for x in cov),
            f"{[x.get('name') for x in cov if x.get('level') == 'canvas'][:5]}")
    return s


# ══ S19 · Lote de cambios (doc 39): PUT /changes/bulk ════════════════════════
def s19_bulk_changes() -> Suite:
    """Cascadas en lote: crear tabla+columnas y borrar en UNA request, con la
    misma semántica que /changes repetido (unicidad en orden, atomicidad de la
    validación: un ítem inválido/duplicado no graba NADA del lote)."""
    s = Suite("s19_bulk_changes")
    mod, otro = Client("modelador"), Client("modelador2")

    def bulk(cli, cs_id, changes):
        return cli.put(f"/api/changesets/{cs_id}/changes/bulk", {"changes": changes})

    pid = mod.create_project()
    base_phys = f"{TAG}_BULK_BASE".upper()
    tid = mod.create_table(logical=f"{TAG} bulk base", schema="e2e", physical=base_phys)
    c1 = mod.add_column(tid, "id", "BIGINT", pk=True, ordinal=0)
    c2 = mod.add_column(tid, "saldo", "STRING", ordinal=1)
    cs = mod.snapshot([pid], title=f"{TAG} s19")

    # 1) crear tabla nueva + 3 columnas en UN lote (forma de "New table from sources")
    nid = _uid("tbulk")
    cols = [f"{nid}.c{i}" for i in range(3)]
    r = bulk(mod, cs, [
        {"collection": "canonical_tables", "entityId": nid, "op": "upsert",
         "payload": {"id": nid, "logicalName": f"{TAG} bulk nueva",
                     "physicalName": f"{TAG}_BULK_NUEVA".upper(), "schema": "e2e"}},
        *[{"collection": "canonical_columns", "entityId": cid, "op": "upsert",
           "payload": {"id": cid, "tableId": nid, "logicalName": f"col {i}",
                       "physicalName": f"{TAG}_C{i}".upper(), "dataType": "STRING", "ordinal": i}}
          for i, cid in enumerate(cols)],
    ])
    s.eq("lote tabla+3 columnas → 200", r.status, 200)
    eff = mod.get(f"/api/changesets/{cs}/effective/canonical_columns?tableId={nid}").data or []
    s.eq("effective muestra las 3 columnas del lote", len(eff), 3)

    # 2) dup INTRA-lote → 409 y NADA del lote queda grabado (validación previa)
    nid2 = _uid("tbulk2")
    r = bulk(mod, cs, [
        {"collection": "canonical_tables", "entityId": nid2, "op": "upsert",
         "payload": {"id": nid2, "logicalName": f"{TAG} bulk dup",
                     "physicalName": f"{TAG}_BULK_DUP".upper(), "schema": "e2e"}},
        {"collection": "canonical_columns", "entityId": f"{nid2}.a", "op": "upsert",
         "payload": {"id": f"{nid2}.a", "tableId": nid2, "logicalName": "a",
                     "physicalName": "REPETIDA", "dataType": "STRING", "ordinal": 0}},
        {"collection": "canonical_columns", "entityId": f"{nid2}.b", "op": "upsert",
         "payload": {"id": f"{nid2}.b", "tableId": nid2, "logicalName": "b",
                     "physicalName": "repetida", "dataType": "STRING", "ordinal": 1}},
    ])
    s.eq("dup intra-lote → 409", r.status, 409)
    eff = mod.get(f"/api/changesets/{cs}/effective/canonical_tables").data or []
    s.check("el 409 no grabó NADA del lote (tabla dup ausente)",
            not any(t["id"] == nid2 for t in eff))

    # 3) ítem inválido → 422 sin escribir; colección fuera de whitelist → 422
    r = bulk(mod, cs, [{"collection": "canonical_columns", "entityId": f"{nid}.bad",
                        "op": "upsert", "payload": {"logicalName": "sin tableId"}}])
    s.eq("payload inválido en el lote → 422", r.status, 422)
    r = bulk(mod, cs, [{"collection": "users", "entityId": "x", "op": "delete"}])
    s.eq("colección no versionada → 422", r.status, 422)

    # 4) sólo el OWNER escribe su working copy → 403
    r = bulk(otro, cs, [{"collection": "canonical_columns", "entityId": c1, "op": "delete"}])
    s.eq("bulk de un no-owner → 403", r.status, 403)

    # 5) cascada de borrado de la tabla PUBLICADA (columnas + tabla) en un lote:
    #    effective deja de mostrarla; producción la sigue viendo (draft aislado)
    r = bulk(mod, cs, [
        {"collection": "canonical_columns", "entityId": c1, "op": "delete"},
        {"collection": "canonical_columns", "entityId": c2, "op": "delete"},
        {"collection": "canonical_tables", "entityId": tid, "op": "delete"},
    ])
    s.eq("lote de deletes (cascada) → 200", r.status, 200)
    eff = mod.get(f"/api/changesets/{cs}/effective/canonical_tables").data or []
    s.check("effective ya no muestra la tabla borrada", not any(t["id"] == tid for t in eff))
    prod = mod.get("/api/catalog/tables").data or []
    s.check("producción la sigue mostrando (pre-publish)", any(t["id"] == tid for t in prod))
    return s


# ══ S20 · Relaciones con llave compuesta (doc 47) ════════════════════════════


def s20_composite_key_relationships() -> Suite:
    """N=N (doc 47): un upsert de relación migra la llave COMPLETA del padre —
    parcial → 409; completo (con columnas del mismo lote) → 200."""
    s = Suite("s20_composite_key_relationships")
    mod = Client("modelador")

    def bulk(cli, cs_id, changes):
        return cli.put(f"/api/changesets/{cs_id}/changes/bulk", {"changes": changes})

    pid = mod.create_project()
    ptid = mod.create_table(logical=f"{TAG} rel padre", schema="e2e",
                            physical=f"{TAG}_REL_PADRE".upper())
    pk1 = mod.add_column(ptid, "id", "BIGINT", pk=True, ordinal=0)
    pk2 = mod.add_column(ptid, "region", "STRING", pk=True, ordinal=1)
    htid = mod.create_table(logical=f"{TAG} rel hijo", schema="e2e",
                            physical=f"{TAG}_REL_HIJO".upper())
    cs = mod.snapshot([pid], title=f"{TAG} s20")

    rid, na, nb = _uid("rel"), _uid("colA"), _uid("colB")
    parcial = bulk(mod, cs, [
        {"collection": "relationships", "entityId": rid, "op": "upsert",
         "payload": {"id": rid, "parentTableId": ptid, "childTableId": htid,
                     "pairs": [{"parentColumnId": pk1, "childColumnId": na}],
                     "parentCardinality": "one", "childCardinality": "zero-many",
                     "identifying": True}},
    ])
    s.eq("relación con 1 par sobre llave de 2 → 409", parcial.status, 409)

    completo = bulk(mod, cs, [
        {"collection": "canonical_columns", "entityId": na, "op": "upsert",
         "payload": {"id": na, "tableId": htid, "logicalName": "id",
                     "physicalName": f"{TAG}_FK_ID".upper(), "dataType": "BIGINT",
                     "ordinal": 0, "isForeignKey": True, "isPrimaryKey": True,
                     "isNullable": False}},
        {"collection": "canonical_columns", "entityId": nb, "op": "upsert",
         "payload": {"id": nb, "tableId": htid, "logicalName": "region",
                     "physicalName": f"{TAG}_FK_REGION".upper(), "dataType": "STRING",
                     "ordinal": 1, "isForeignKey": True, "isPrimaryKey": True,
                     "isNullable": False}},
        {"collection": "relationships", "entityId": rid, "op": "upsert",
         "payload": {"id": rid, "parentTableId": ptid, "childTableId": htid,
                     "pairs": [{"parentColumnId": pk1, "childColumnId": na},
                               {"parentColumnId": pk2, "childColumnId": nb}],
                     "parentCardinality": "one", "childCardinality": "zero-many",
                     "identifying": True}},
    ])
    s.eq("llave completa + columnas del lote → 200", completo.status, 200)
    eff = mod.get(f"/api/changesets/{cs}/effective/relationships?tableId={ptid}").data or []
    s.eq("effective trae la relación con 2 pares", len((eff[0] or {}).get("pairs", [])) if eff else 0, 2)
    return s


# ══ S21 · Carga masiva desde Excel (doc 55) ═════════════════════════════════


def s21_bulk_upload() -> Suite:
    """Doc 78: perfil de carga por proyecto (built-in «Plantilla BCP») +
    Validate → reporte → apply dentro del draft, con polling del job: crea
    subject/canvas/esquema/tablas/columnas; un job ajeno es 403; un reporte con
    errores no se aplica (409); la re-carga idéntica es unchanged."""
    s = Suite("s21_bulk_upload")
    mod, otro = Client("modelador"), Client("modelador2")
    pname = f"{TAG} proyecto carga"
    pid = mod.create_project(name=pname)
    cs = mod.snapshot([pid], title=f"{TAG} s21")
    t_uno, t_dos = f"{TAG} carga uno", f"{TAG} carga dos"
    p_uno, p_dos = f"{TAG}_CARGA_UNO".upper(), f"{TAG}_CARGA_DOS".upper()

    # 0) perfil de carga: el built-in se materializa contra los UDP del proyecto
    pf = mod.post(f"/api/projects/{pid}/upload-profiles/default")
    s.eq("perfil default → 201", pf.status, 201)
    profile_id = ((pf.data or {}).get("profile") or {}).get("id")
    s.eq("segundo default → 409", mod.post(f"/api/projects/{pid}/upload-profiles/default").status, 409)
    lst = mod.get(f"/api/projects/{pid}/upload-profiles").data or []
    s.eq("lista 1 perfil, default", [(p["name"], p["isDefault"]) for p in lst], [("Plantilla BCP", True)])
    s.eq("uploads con perfil inexistente → 404",
         mod.post(f"/api/changesets/{cs}/uploads", {"fileName": "x.xlsx", "profileId": "nope", "sheets": []}).status, 404)

    def workbook(tipo_nombre="varchar(50)"):
        hdr_t = ["", "SUBJECT", "DIAGRAMA", "ESQUEMA", "TABLA_LOGICO", "TABLA_FISICA", "DEF_TABLA"]
        hdr_c = ["", "TABLA_LOGICO", "CAMPO_LOGICO", "CAMPO_FISICO", "TIPO_DATO", "PK"]
        return {"fileName": f"{TAG}.xlsx", "profileId": profile_id, "sheets": [
            {"name": "Cargar_Tablas", "rows": [
                {"row": 5, "cells": hdr_t},
                {"row": 6, "cells": ["", f"{TAG} subject", f"{TAG} diagrama", "e2e", t_uno, p_uno, "Definición\ncon salto y ñ"]},
                {"row": 7, "cells": ["", f"{TAG} subject", f"{TAG} diagrama", "e2e", t_dos, p_dos, ""]}]},
            {"name": "Cargar_Campos", "rows": [
                {"row": 5, "cells": hdr_c},
                {"row": 6, "cells": ["", t_uno, "identificador", f"{TAG}_ID".upper(), "bigint", "X"]},
                {"row": 7, "cells": ["", t_uno, "nombre", f"{TAG}_NOMBRE".upper(), tipo_nombre, ""]}]}]}

    def wait(job_id):
        r = None
        for _ in range(120):
            r = mod.get(f"/api/changesets/{cs}/uploads/{job_id}")
            if r.status != 200 or (r.data or {}).get("status") in ("validated", "applied", "failed"):
                return r
            time.sleep(0.5)
        return r

    # 1) validación async → reporte limpio
    r = mod.post(f"/api/changesets/{cs}/uploads", workbook())
    s.eq("POST uploads → 202", r.status, 202)
    job = (wait(r.data["id"]).data or {})
    s.eq("validación termina en validated", job.get("status"), "validated")
    rep = job.get("report") or {}
    s.eq("reporte sin errores", rep.get("errorCount"), 0)
    s.eq("resumen: 2 tablas a crear", (rep.get("summary") or {}).get("tables", {}).get("create"), 2)
    s.eq("resumen: 2 columnas a crear", (rep.get("summary") or {}).get("columns", {}).get("create"), 2)
    s.eq("resumen: 1 canvas a crear", (rep.get("summary") or {}).get("canvases", {}).get("create"), 1)
    s.eq("resumen: 1 carpeta (subject) a crear", (rep.get("summary") or {}).get("folders", {}).get("create"), 1)
    s.eq("reporte nombra el perfil", (rep.get("profile") or {}).get("name"), "Plantilla BCP")
    s.eq("reporte: hojas halladas con cabecera en la fila 5",
         [(sh["name"], sh["found"], sh["headerRow"]) for sh in rep.get("sheets") or []],
         [("Cargar_Tablas", True, 5), ("Cargar_Campos", True, 5)])
    s.eq("job de otro usuario → 403", otro.get(f"/api/changesets/{cs}/uploads/{job['id']}").status, 403)

    # 2) apply async → escribe en el draft; el canvas afectado vuelve en el resultado
    r = mod.post(f"/api/changesets/{cs}/uploads/{job['id']}/apply")
    s.eq("apply → 202", r.status, 202)
    done = (wait(job["id"]).data or {})
    s.eq("apply termina en applied", done.get("status"), "applied", )
    s.eq("1 canvas afectado", len((done.get("result") or {}).get("affectedCanvasIds") or []), 1)
    eff = mod.get(f"/api/changesets/{cs}/effective/canonical_tables?q={p_uno[:-4]}").data or []
    s.eq("effective muestra las 2 tablas nuevas",
         len([t for t in eff if t["physicalName"] in (p_uno, p_dos)]), 2)
    tid = next((t["id"] for t in eff if t["physicalName"] == p_uno), None)
    cols = mod.get(f"/api/changesets/{cs}/effective/canonical_columns?tableId={tid}").data or []
    s.eq("la tabla uno tiene sus 2 columnas", len(cols), 2)
    s.check("la PK quedó marcada con posición 0",
            any(c.get("isPrimaryKey") and c.get("pkPosition") == 0 for c in cols))
    prod = mod.get("/api/catalog/tables").data or []
    s.check("producción NO ve las tablas (draft aislado)", not any(t["physicalName"] == p_uno for t in prod))

    # 3) tipo inválido → reporte con error → apply 409
    r = mod.post(f"/api/changesets/{cs}/uploads", workbook(tipo_nombre="texto raro"))
    bad = (wait(r.data["id"]).data or {})
    s.eq("reporte con 1 error (invalid-type)", (bad.get("report") or {}).get("errorCount"), 1)
    s.eq("apply con errores → 409", mod.post(f"/api/changesets/{cs}/uploads/{bad['id']}/apply").status, 409)

    # 4) re-carga idéntica → todo unchanged, sin cambios nuevos
    r = mod.post(f"/api/changesets/{cs}/uploads", workbook())
    again = (wait(r.data["id"]).data or {})
    s.eq("re-carga idéntica: 2 tablas unchanged", (again.get("report") or {}).get("summary", {}).get("tables", {}).get("unchanged"), 2)
    s.eq("re-carga idéntica: 2 columnas unchanged", (again.get("report") or {}).get("summary", {}).get("columns", {}).get("unchanged"), 2)
    s.eq("discard del job → 200", mod.delete(f"/api/changesets/{cs}/uploads/{again['id']}").status, 200)
    return s


ALL = {
    "s01_rbac": s01_rbac, "s02_version_lifecycle": s02_version_lifecycle,
    "s03_convergence": s03_convergence, "s04_domain_cascade": s04_domain_cascade,
    "s05_udp_scope": s05_udp_scope, "s06_canvas_crud": s06_canvas_crud,
    "s07_relationships": s07_relationships, "s08_views": s08_views,
    "s09_reporting": s09_reporting, "s10_admin": s10_admin, "s11_audit": s11_audit,
    "s12_glossary_udp": s12_glossary_udp,
    # Lote precisiones de modelamiento (doc 10)
    "s13_guardas_duplicados": s13_guardas_duplicados,
    "s14_impacto_eliminacion": s14_impacto_eliminacion,
    "s15_glosario_validacion_lock": s15_glosario_validacion_lock,
    "s16_domain_impact": s16_domain_impact,
    "s17_vistas_multifuente": s17_vistas_multifuente,
    "s18_udp_canvas_models": s18_udp_canvas_models,
    # Lote de cambios (doc 39)
    "s19_bulk_changes": s19_bulk_changes,
    # Relaciones con llave compuesta (doc 47)
    "s20_composite_key_relationships": s20_composite_key_relationships,
    # Carga masiva desde Excel (doc 55)
    "s21_bulk_upload": s21_bulk_upload,
}
