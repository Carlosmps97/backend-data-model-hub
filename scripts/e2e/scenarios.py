"""Escenarios E2E contra el backend (vivo o en memoria). Cada `sNN_*` devuelve un `Suite`.

Todos usan fixtures AISLADOS: un proyecto propio por escenario y el tag único
del proceso; limpian al final. Doc 105: el modelo se escribe SIEMPRE por una
versión (`Seed` y los drafts del escenario) y los estándares por
`standards/apply`; las escrituras directas a producción se verifican cerradas.
Correr:
    .venv/bin/python -m scripts.e2e.run_e2e <scenario>       # uno
    .venv/bin/python -m scripts.e2e.run_e2e all              # todos (serial)
"""
from __future__ import annotations

import time
import uuid

from scripts.e2e.harness import TAG, Client, Seed, Suite, _uid, phys

VERSION_REQUIRED = "This change requires a version in edit mode."
STANDARDS_VERSIONED = ("Standards changes go through Data Standards (Save & apply) so they keep "
                       "a version and can be rolled back.")


def _by_id(rows, eid):
    return next((r for r in (rows or []) if r.get("id") == eid), None)


# ══ S01 · RBAC por rol (matriz de permisos sobre endpoints reales) ═══════════
def s01_rbac() -> Suite:
    s = Suite("s01_rbac")
    admin, mod, rev, lec = Client("admin"), Client("modelador"), Client("revisor"), Client("lector")
    pid = admin.create_project()

    # /auth/me (login) devuelve los permisos correctos por rol
    s.eq("admin access_level=full", admin.me.get("accessLevel"), "full")
    s.check("lector solo view+export", {k for k, v in lec.me["permissions"].items() if v} == {"model.view", "export"})

    # model.edit — el modelo se escribe por versiones
    r = lec.post("/api/changesets/snapshot", {"projectId": pid, "title": f"{TAG} x"})
    s.eq("lector NO puede abrir una versión (model.edit)", r.status, 403)
    r = mod.post("/api/changesets/snapshot", {"projectId": pid, "title": f"{TAG} x"})
    s.check("modelador SÍ puede abrir una versión", r.status in (200, 201), f"status={r.status}")
    cs = (r.data or {}).get("id")

    # Doc 105: escribir DIRECTO a producción — sin permiso 403; con permiso 409.
    table = {"logicalName": f"{TAG} rbac", "physicalName": phys(f"{TAG} rbac"), "schema": "e2e"}
    for name, cli, exp in (("lector", lec, 403), ("modelador", mod, 409)):
        rt = cli.post(f"/api/projects/{pid}/catalog/tables", table)
        s.eq(f"{name} crear tabla directa → {exp}", rt.status, exp)
        rv = cli.post("/api/views", {"name": phys(_uid("v")), "schema": "e2e", "sourceTableIds": []})
        s.eq(f"{name} crear vista directa → {exp}", rv.status, exp)
    s.eq("la escritura directa pide una versión", rt.detail, VERSION_REQUIRED)

    # standards.edit — sólo admin; y aun así, sólo por Data Standards (versionado)
    body = {"kind": "domain", "domainsUpsert": [{"name": f"{TAG} nop", "defaultDataType": "STRING"}]}
    s.eq("modelador NO puede standards.apply", mod.apply_standards(pid, body).status, 403)
    s.eq("lector NO puede standards.apply", lec.apply_standards(pid, body).status, 403)
    r = admin.post(f"/api/projects/{pid}/domains", {"name": f"{TAG} nop", "defaultDataType": "STRING"})
    s.check("admin: alta DIRECTA de dominio → 409 (va por Data Standards)",
            r.status == 409 and r.detail == STANDARDS_VERSIONED, f"status={r.status} {r.detail}")

    # admin.manage — solo admin
    s.eq("modelador NO puede listar users (admin)", mod.get("/api/admin/users").status, 403)
    s.eq("revisor NO puede listar users (admin)", rev.get("/api/admin/users").status, 403)
    s.check("admin SÍ lista users", admin.get("/api/admin/users").status == 200)

    # review.decide — modelador no puede aprobar
    if cs:
        s.eq("modelador NO puede aprobar (review.decide)",
             mod.post(f"/api/changesets/{cs}/review", {"decision": "approve"}).status, 403)
    return s


# ══ S02 · Ciclo de versión completo (snapshot→cambios→submit→approve→prod) ═══
def s02_version_lifecycle() -> Suite:
    s = Suite("s02_version_lifecycle")
    mod, rev = Client("modelador"), Client("revisor")

    # Producción base: proyecto + tabla publicada por una versión.
    pid = mod.create_project()
    seed = Seed(mod, pid)
    tid = seed.table(f"{TAG} lifecycle")
    seed.column(tid, "id", "BIGINT", pk=True)
    seed.publish(rev)

    # 1) draft, 2) editar la tabla (nueva descripción) + nueva columna dentro del draft
    cs = mod.draft(pid, f"{TAG} v-lifecycle")
    s.check("snapshot creó draft", bool(cs))
    tdoc = mod.table(pid, tid)
    r = mod.change(cs, "canonical_tables", tid, {**tdoc, "description": "EDITADO en draft"})
    s.eq("add_change tabla (edit)", r.status, 200)
    new_col = _uid("col")
    r = mod.change(cs, "canonical_columns", new_col,
                   {"tableId": tid, "logicalName": "columna nueva", "physicalName": "COL_NUEVA",
                    "dataType": "STRING", "ordinal": 1})
    s.eq("add_change columna nueva", r.status, 200)

    # producción todavía NO ve el cambio (draft aislado)
    s.check("producción NO ve el edit del draft",
            (mod.table(pid, tid) or {}).get("description") != "EDITADO en draft")

    # 3) submit con revisor, 4) el revisor aprueba → aplica a producción
    r = mod.post(f"/api/changesets/{cs}/submit", {"reviewers": [rev.user], "title": f"{TAG} req"})
    s.eq("submit a revisión", r.status, 200)
    r = rev.post(f"/api/changesets/{cs}/review", {"decision": "approve"})
    s.check("revisor aprueba+publica", r.status == 200 and (r.data or {}).get("status") == "approved",
            f"status={r.status} {str(r.raw)[:150]}")

    # 5) producción AHORA refleja el cambio + la columna nueva
    s.eq("producción refleja el edit", (mod.table(pid, tid) or {}).get("description"), "EDITADO en draft")
    cols = mod.columns(tid)
    s.check("columna nueva publicada", _by_id(cols, new_col) is not None, f"{len(cols)} cols")

    # 6) la versión de producción del proyecto es la nueva
    pub = mod.get(f"/api/projects/{pid}/versions/published").data
    s.eq("la versión publicada es la del ciclo", (pub or {}).get("id"), cs)
    return s


# ══ S03 · Convergencia / merge: dos drafts editan la MISMA tabla ═════════════
def s03_convergence() -> Suite:
    s = Suite("s03_convergence")
    modA, modB, rev = Client("modelador"), Client("modelador2"), Client("revisor")
    pid = modA.create_project()
    seed = Seed(modA, pid)
    tid = seed.table(f"{TAG} shared")
    seed.column(tid, "id", "BIGINT", pk=True)
    seed.publish(rev)
    tdoc = modA.table(pid, tid)

    csA = modA.draft(pid, f"{TAG} draftA")
    csB = modB.draft(pid, f"{TAG} draftB")
    s.check("dos drafts creados desde la misma producción", bool(csA and csB))

    # A y B editan la MISMA tabla con valores distintos
    modA.change(csA, "canonical_tables", tid, {**tdoc, "description": "valor A"})
    modB.change(csB, "canonical_tables", tid, {**tdoc, "description": "valor B"})

    # publicar A
    s.eq("publica draft A", modA.publish(csA, rev).status, 200)
    s.eq("producción = valor A tras publicar A", (modA.table(pid, tid) or {}).get("description"), "valor A")

    # El draft B conserva su override (overlay vivo por entidad)
    effB = modB.get(f"/api/changesets/{csB}/effective/canonical_tables?ids={tid}").data
    s.check("draft B conserva su propio override (valor B)",
            (_by_id(effB, tid) or {}).get("description") == "valor B",
            f"B ve: {(_by_id(effB, tid) or {}).get('description')!r}")

    # publicar B → converge a valor B (last-writer-wins sobre esa tabla)
    s.eq("publica draft B", modB.publish(csB, rev).status, 200)
    s.eq("producción converge a valor B (last-writer-wins)",
         (modB.table(pid, tid) or {}).get("description"), "valor B")
    return s


# ══ S04 · Cascada de ParentDomain (R7): prod vs draft vs in-progress ═════════
def s04_domain_cascade() -> Suite:
    s = Suite("s04_domain_cascade")
    admin, mod, rev = Client("admin"), Client("modelador"), Client("revisor")

    # dominio del proyecto (Data Standards, versionado) + columna que lo sigue
    pid = mod.create_project()
    dname = f"{TAG} dom"
    dom = admin.create_domain(pid, dname, "DECIMAL(10,2)")
    seed = Seed(mod, pid)
    tid = seed.table(f"{TAG} casc")
    cid = seed.column(tid, "importe", "DECIMAL(10,2)", domain=dom)   # sin override: sigue al dominio
    seed.publish(rev)

    def col_type_prod():
        return (_by_id(mod.columns(tid), cid) or {}).get("dataType")

    s.eq("tipo inicial en producción", col_type_prod(), "DECIMAL(10,2)")

    # draft (copy-on-write) e in-progress (submitted) abiertos ANTES del cambio
    cs_draft = mod.draft(pid, f"{TAG} pre-cambio")
    cs_prog = mod.draft(pid, f"{TAG} in-progress")
    mod.post(f"/api/changesets/{cs_prog}/submit", {"reviewers": [rev.user]})

    # cambio de estándar: dominio → BIGINT (cascada a las columnas que lo siguen)
    base = admin.get(f"/api/projects/{pid}/standards/versions").data or []
    r = admin.apply_standards(pid, {"kind": "domain",
                                    "domainsUpsert": [{"id": dom, "name": dname, "defaultDataType": "BIGINT"}]})
    s.check("standards.apply dominio→BIGINT", r.status == 200, f"status={r.status} {str(r.raw)[:120]}")
    s.eq("producción re-tipada a BIGINT (cascada)", col_type_prod(), "BIGINT")

    # draft e in-progress sin override de esa columna: ven el publicado nuevo
    for label, cs in (("draft", cs_draft), ("in-progress", cs_prog)):
        eff = mod.get(f"/api/changesets/{cs}/effective/canonical_columns?tableId={tid}").data
        s.check(f"{label} sin override ve el tipo nuevo (BIGINT)",
                (_by_id(eff, cid) or {}).get("dataType") == "BIGINT",
                f"{label} ve: {(_by_id(eff, cid) or {}).get('dataType')!r}")

    # rollback de Data Standards a la versión previa → la cascada vuelve
    target = max(v["seq"] for v in base)
    r = admin.post(f"/api/projects/{pid}/standards/rollback", {"targetSeq": target})
    s.check("rollback de estándares", r.status == 200, f"status={r.status} {str(r.raw)[:120]}")
    s.eq("producción restaurada a DECIMAL(10,2)", col_type_prod(), "DECIMAL(10,2)")
    return s


# ══ S05 · Estándares: lecturas del módulo + dry-run del re-derivado (NO escribe) ═
def s05_udp_scope() -> Suite:
    s = Suite("s05_udp_scope")
    admin, mod, rev, lec = Client("admin"), Client("modelador"), Client("revisor"), Client("lector")
    pid = mod.create_project()
    # El físico sale de la regla vigente (physicalize): una columna con otro
    # físico sería un override manual (doc 68) y el re-derivado no la toca.
    derived = (mod.post(f"/api/projects/{pid}/glossary/physicalize",
                        {"logical": "saldo cliente", "scope": "column"}).data or {}).get("physical")
    seed = Seed(mod, pid)
    tid = seed.table(f"{TAG} saldos")
    seed.column(tid, "saldo cliente", "DECIMAL(18,2)", physical=derived)
    seed.publish(rev)

    snap = admin.get(f"/api/projects/{pid}/standards/snapshot")
    s.check("standards/snapshot lee OK", snap.status == 200, f"status={snap.status}")
    s.check("snapshot trae domains+dict+namingConfig+udp",
            all(k in (snap.data or {}) for k in ("domains", "dict", "namingConfig", "udp")))
    vers = admin.get(f"/api/projects/{pid}/standards/versions")
    s.check("standards/versions lee OK", vers.status == 200, f"{len(vers.data or [])} versiones")
    s.eq("lector también lee el historial", lec.get(f"/api/projects/{pid}/standards/versions").status, 200)

    # Dry-run (docs 94 D7 · 95 D3): qué físicos renombraría un término nuevo, SIN escribir.
    r = admin.post(f"/api/projects/{pid}/glossary/impact",
                   {"scope": "column", "termsUpsert": [{"term": "saldo", "abbrev": "SLD"}], "termsDelete": []})
    rows = (r.data or {}).get("renamed") or []
    s.check("dry-run lista el renombre que haría el término", r.status == 200 and bool(derived)
            and any(x.get("from") == derived and x.get("to", "").startswith("SLD") for x in rows),
            f"status={r.status} derived={derived} renamed={rows[:2]}")
    s.eq("el dry-run no registra versión", len(admin.get(f"/api/projects/{pid}/standards/versions").data or []),
         len(vers.data or []))
    s.check("el dry-run no renombra producción", any(c["physicalName"] == derived for c in mod.columns(tid)))
    return s


# ══ S06 · Canvas por versión (crear/editar/eliminar) ══════════════════════════
def s06_canvas_crud() -> Suite:
    s = Suite("s06_canvas_crud")
    mod, rev = Client("modelador"), Client("revisor")
    pid = mod.create_project()
    seed = Seed(mod, pid)
    t1 = seed.table(f"{TAG} c1"); seed.column(t1, "id", "BIGINT", pk=True)
    t2 = seed.table(f"{TAG} c2"); seed.column(t2, "id", "BIGINT", pk=True)
    ca = seed.canvas(f"{TAG} canvas", table_ids=[t1, t2])
    seed.publish(rev)

    diag = mod.get(f"/api/subject-areas/{ca}/diagram").data or {}
    s.eq("diagrama tiene 2 tablas", len(diag.get("tables", [])), 2)
    # Doc 105: ni tablas ni layout del canvas se escriben directo en producción
    s.eq("PUT directo de tablas del canvas → 409",
         mod.put(f"/api/subject-areas/{ca}/tables", {"tableIds": [t1]}).status, 409)
    s.eq("PUT directo del layout → 409",
         mod.put(f"/api/subject-areas/{ca}/layout", {"layout": {}}).status, 409)

    # editar por versión: quitar una tabla
    sa = mod.get(f"/api/subject-areas/{ca}").data
    cs = mod.draft(pid, f"{TAG} quitar tabla")
    r = mod.change(cs, "subject_areas", ca, {**sa, "tableIds": [t1], "layout": {t1: sa["layout"][t1]}})
    s.eq("quitar una tabla en el draft", r.status, 200)
    s.eq("publicar el cambio del canvas", mod.publish(cs, rev).status, 200)
    diag = mod.get(f"/api/subject-areas/{ca}/diagram").data or {}
    s.eq("diagrama ahora tiene 1 tabla", len(diag.get("tables", [])), 1)

    # eliminar por versión
    cs = mod.draft(pid, f"{TAG} borrar canvas")
    s.eq("borrar el canvas en el draft", mod.change(cs, "subject_areas", ca, None, op="delete").status, 200)
    s.eq("publicar el borrado", mod.publish(cs, rev).status, 200)
    s.eq("canvas eliminado (404)", mod.get(f"/api/subject-areas/{ca}").status, 404)
    return s


# ══ S07 · Relaciones por versión (crear / listar / eliminar) ═════════════════
def s07_relationships() -> Suite:
    s = Suite("s07_relationships")
    mod, rev = Client("modelador"), Client("revisor")
    pid = mod.create_project()
    seed = Seed(mod, pid)
    t1 = seed.table(f"{TAG} r padre"); c1 = seed.column(t1, "id", "BIGINT", pk=True)
    t2 = seed.table(f"{TAG} r hijo"); c2 = seed.column(t2, "id padre", "BIGINT", isForeignKey=True)
    rid = seed.relationship(t1, c1, t2, c2)
    seed.publish(rev)

    def listed():
        return any(r["id"] == rid for r in (mod.get(f"/api/relationships?tableId={t1}").data or []))

    s.check("relación publicada aparece en el listado", listed())
    s.eq("borrar directo en producción → 409", mod.delete(f"/api/relationships/{rid}").status, 409)
    cs = mod.draft(pid, f"{TAG} borrar relación")
    s.eq("borrar la relación en el draft", mod.change(cs, "relationships", rid, None, op="delete").status, 200)
    s.check("producción la sigue viendo antes de publicar", listed())
    s.eq("publicar el borrado", mod.publish(cs, rev).status, 200)
    s.check("relación ya no está", not listed())
    return s


# ══ S08 · Vistas por versión (crear/leer/editar/eliminar) ═════════════════════
def s08_views() -> Suite:
    s = Suite("s08_views")
    mod, rev = Client("modelador"), Client("revisor")
    pid = mod.create_project()
    seed = Seed(mod, pid)
    t1 = seed.table(f"{TAG} vbase"); seed.column(t1, "id", "BIGINT", pk=True, physical="ID")
    seed.publish(rev)

    def published():
        return _by_id(mod.get(f"/api/views?projectId={pid}").data, vid)

    vid = _uid("view")
    body = {"name": phys(_uid("vw")), "schema": "e2e_vu", "sourceTableIds": [t1],
            "sources": [{"tableId": t1, "column": "ID"}]}
    cs = mod.draft(pid, f"{TAG} vista")
    s.eq("vista creada en el draft", mod.change(cs, "views", vid, body).status, 200)
    s.check("producción NO la ve antes de publicar", published() is None)
    s.eq("publicar la vista", mod.publish(cs, rev).status, 200)
    s.check("vista publicada en el listado", published() is not None)
    s.eq("editar directo en producción → 409", mod.put(f"/api/views/{vid}", body).status, 409)

    cs = mod.draft(pid, f"{TAG} editar vista")
    s.eq("editar en el draft", mod.change(cs, "views", vid, {**body, "description": "editada"}).status, 200)
    s.eq("publicar la edición", mod.publish(cs, rev).status, 200)
    s.eq("producción refleja la edición", (published() or {}).get("description"), "editada")

    cs = mod.draft(pid, f"{TAG} borrar vista")
    s.eq("borrar en el draft", mod.change(cs, "views", vid, None, op="delete").status, 200)
    s.eq("publicar el borrado", mod.publish(cs, rev).status, 200)
    s.check("vista eliminada", published() is None)
    return s


# ══ S09 · Reporting (exactitud sobre fixture conocido) ═══════════════════════
def s09_reporting() -> Suite:
    s = Suite("s09_reporting")
    mod, rev = Client("modelador"), Client("revisor")
    pid = mod.create_project()
    seed = Seed(mod, pid)
    tid = seed.table(f"{TAG} rep", schema="e2erep")
    for i in range(3):
        seed.column(tid, f"col{i}", "STRING", ordinal=i)
    fname, cname = f"{TAG} subject rep", f"{TAG} repcanvas"
    seed.canvas(cname, table_ids=[tid], folder_id=seed.folder(fname))
    seed.publish(rev)

    # /reporting/tables filtrado por schema → 1 fila con columnCount=3
    row = _by_id(mod.get(f"/api/reporting/tables?projectId={pid}&schema=e2erep").data, tid)
    s.check("tabla aparece en reporting", bool(row))
    if row:
        s.eq("columnCount = 3", row.get("columnCount"), 3)
        # Doc 88 §7: subjectAreas = carpetas de los canvases; diagrams = los canvases.
        s.check("el canvas aparece en diagrams", cname in (row.get("diagrams") or []),
                f"diagrams={row.get('diagrams')}")
        s.check("su carpeta aparece en subjectAreas", fname in (row.get("subjectAreas") or []),
                f"subjectAreas={row.get('subjectAreas')}")
    # /reporting/columns acotado a la tabla → 3 filas
    cols = mod.get(f"/api/reporting/columns?projectId={pid}&tableIds={tid}").data
    s.eq("reporting columns = 3", len([c for c in (cols or []) if c["tableId"] == tid]), 3)
    return s


# ══ S10 · Admin: users CRUD + guards anti-lockout ════════════════════════════
def s10_admin() -> Suite:
    from scripts.e2e import harness as H
    s = Suite("s10_admin")
    admin = Client("admin")
    uname = f"e2euser_{uuid.uuid4().hex[:6]}"
    rkey = f"e2erole_{uuid.uuid4().hex[:6]}"
    H._track("users", uname); H._track("roles", rkey)
    # crear usuario LOCAL (con contraseña; los correos entran por SSO)
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
    s.check("registra login", "login" in actions, f"acciones muestra={list(actions)[:8]}")
    s.check("registra login_failed", "login_failed" in actions)
    s.check("cada entrada tiene actor+action+timestamp",
            all(e.get("actor") and e.get("action") and (e.get("at") or e.get("timestamp")) for e in entries[:20]))
    # lector NO puede leer auditoría
    s.eq("lector NO lee audit (admin.manage)", Client("lector").get("/api/admin/audit").status, 403)
    return s


# ══ S12 · Glossary + UDP real (definir por Data Standards / asignar por versión / rollback) ═
def s12_glossary_udp() -> Suite:
    s = Suite("s12_glossary_udp")
    admin, mod, rev = Client("admin"), Client("modelador"), Client("revisor")
    pid = mod.create_project()

    s.eq("GET glossary del proyecto responde", admin.get(f"/api/projects/{pid}/glossary?scope=column").status, 200)
    s.eq("GET /api/dictionary (viejo) → 404", admin.get("/api/dictionary").status, 404)

    # UDP: definir keys versionadas (kind='udp'), una a nivel COLUMNA y otra a nivel TABLA.
    cname, tname = f"{TAG} ClasifCol", f"{TAG} DomTbl"
    d = admin.apply_standards(pid, {"kind": "udp", "udpUpsert": [
        {"name": cname, "level": "column", "dataType": "list", "allowedValues": ["DAC", "NO DAC"],
         "defaultValue": "NO DAC"},
        {"name": tname, "level": "table", "dataType": "string"}]})
    s.check("UDP keys definidas (versión kind=udp)", d.status == 200 and (d.data or {}).get("kind") == "udp",
            f"v={(d.data or {}).get('label')}")
    defs = admin.get(f"/api/projects/{pid}/udp").data or []
    cdef = next((x for x in defs if x["name"] == cname), None)
    tdef = next((x for x in defs if x["name"] == tname), None)
    s.check("UDP columna con level+allowedValues",
            bool(cdef) and cdef["level"] == "column" and cdef["allowedValues"] == ["DAC", "NO DAC"])
    s.check("UDP tabla con level=table (Text)", bool(tdef) and tdef["level"] == "table" and tdef["dataType"] == "string")
    cdid = cdef["id"] if cdef else "x"
    tdid = tdef["id"] if tdef else "y"

    # Asignar valores UDP a una COLUMNA y a la TABLA por versión → publicar → verificar.
    seed = Seed(mod, pid)
    tid = seed.table(f"{TAG} udp")
    cid = seed.column(tid, "col", "STRING")
    seed.publish(rev)
    cdoc = _by_id(mod.columns(tid), cid) or {}
    tdoc = mod.table(pid, tid) or {}
    cs = mod.draft(pid, f"{TAG} udp")
    s.eq("add_change columna con udpValues",
         mod.change(cs, "canonical_columns", cid, {**cdoc, "udpValues": {cdid: "DAC"}}).status, 200)
    mod.change(cs, "canonical_tables", tid, {**tdoc, "udpValues": {tdid: "ref_x"}})
    s.eq("publicar las asignaciones", mod.publish(cs, rev).status, 200)
    pub = _by_id(mod.columns(tid), cid) or {}
    s.check("columna publicada con udpValues (col)", (pub.get("udpValues") or {}).get(cdid) == "DAC",
            f"{pub.get('udpValues')}")
    pubt = mod.table(pid, tid) or {}
    s.check("tabla publicada con udpValues (tabla)", (pubt.get("udpValues") or {}).get(tdid) == "ref_x",
            f"{pubt.get('udpValues')}")

    # Rollback de Data Standards a la versión previa: quita las keys sin re-derivar nombres.
    vers = sorted(admin.get(f"/api/projects/{pid}/standards/versions").data or [], key=lambda v: v["seq"])
    target = vers[-2]["seq"] if len(vers) >= 2 else None
    rb = admin.post(f"/api/projects/{pid}/standards/rollback", {"targetSeq": target})
    after = admin.get(f"/api/projects/{pid}/udp").data or []
    s.check("rollback UDP quita las keys nuevas",
            rb.status == 200 and not any(x["name"] in (cname, tname) for x in after), f"status={rb.status}")
    s.check("rollback UDP NO re-deriva nombres (impact columns=0)",
            (rb.data or {}).get("impact", {}).get("columns") == 0, f"impact={(rb.data or {}).get('impact')}")
    return s


# ═════════════════════════════════════════════════════════════════════════════
# Lote "precisiones de modelamiento" (spec doc 10) — s13..s18
# ═════════════════════════════════════════════════════════════════════════════

# ══ S13 · #9 Duplicados: tabla (nombre físico, por proyecto) y columna (por tabla) → 409 ═
def s13_guardas_duplicados() -> Suite:
    s = Suite("s13_guardas_duplicados")
    mod, rev = Client("modelador"), Client("revisor")

    pid = mod.create_project()
    base = f"{TAG}_DUP_BASE".upper()
    seed = Seed(mod, pid)
    tid = seed.table(f"{TAG} dup base", physical=base)
    cid = seed.column(tid, "codigo cliente", "STRING", physical="CODIGO_CLIENTE")
    seed.publish(rev)
    s.check("fixture publicado (tabla+columna)", _by_id(mod.columns(tid), cid) is not None)

    def table(name, schema="e2e"):
        return {"logicalName": f"{TAG} otra", "physicalName": name, "schema": schema}

    cs = mod.draft(pid, f"{TAG} s13")
    nid = _uid("tdup")
    # 1) mismo physicalName EXACTO contra publicado → 409
    s.eq("tabla duplicada exacta → 409", mod.change(cs, "canonical_tables", nid, table(base)).status, 409)
    # 2) case-insensitive → 409
    s.eq("tabla duplicada case-insensitive → 409",
         mod.change(cs, "canonical_tables", nid, table(base.lower())).status, 409)
    # 3) doc 50: el físico es único POR PROYECTO — otro esquema también choca
    s.eq("mismo nombre en otro schema → 409",
         mod.change(cs, "canonical_tables", nid, table(base, "e2e_otro")).status, 409)
    # 4) nombre libre → OK; pendiente-vs-pendiente en el MISMO changeset → 409
    s.eq("nombre libre → OK", mod.change(cs, "canonical_tables", nid, table(f"{base}_OTRA")).status, 200)
    s.eq("duplicado contra pendiente del mismo draft → 409",
         mod.change(cs, "canonical_tables", _uid("tdup2"), table(f"{base}_OTRA")).status, 409)
    # 5) columna duplicada dentro de la misma tabla (case-insensitive) → 409
    ncid = _uid("dupcol")
    col = {"tableId": tid, "logicalName": "codigo cliente bis", "physicalName": "codigo_cliente",
           "dataType": "STRING", "ordinal": 9}
    s.eq("columna duplicada (case-insensitive) → 409", mod.change(cs, "canonical_columns", ncid, col).status, 409)
    # 6) columna con nombre nuevo → OK
    s.eq("columna con nombre libre → OK", mod.change(
        cs, "canonical_columns", ncid, {**col, "logicalName": "columna sana", "physicalName": "SANA"}).status, 200)

    # 7) carrera entre changesets: B mete el mismo nombre ANTES de que A publique
    #    → el re-chequeo del publish (approve) de B responde 409.
    csA = mod.draft(pid, f"{TAG} s13-A")
    csB = mod.draft(pid, f"{TAG} s13-B")
    race = f"{TAG}_RACE".upper()
    s.eq("csA registra tabla RACE", mod.change(csA, "canonical_tables", _uid("raceA"), table(race)).status, 200)
    s.eq("csB registra tabla RACE (aún sin conflicto publicado)",
         mod.change(csB, "canonical_tables", _uid("raceB"), table(race)).status, 200)
    s.eq("csA publica OK", mod.publish(csA, rev).status, 200)
    r = mod.publish(csB, rev)
    s.check("re-chequeo del publish atrapa la carrera → 409", r.status == 409, f"status={r.status} {str(r.raw)[:120]}")
    return s


# ══ S14 · #8 Impacto global al eliminar columna con relaciones ═══════════════
def s14_impacto_eliminacion() -> Suite:
    s = Suite("s14_impacto_eliminacion")
    mod, rev = Client("modelador"), Client("revisor")

    pid = mod.create_project()
    cname = f"{TAG} imp-canvas"
    seed = Seed(mod, pid)
    t1 = seed.table(f"{TAG} cliente")
    c1 = seed.column(t1, "id cliente", "BIGINT", pk=True, ordinal=0)
    t2 = seed.table(f"{TAG} cuenta")
    c2 = seed.column(t2, "id cliente", "BIGINT", ordinal=0, isForeignKey=True)
    seed.relationship(t1, c1, t2, c2)
    seed.canvas(cname, table_ids=[t1, t2])
    seed.publish(rev)

    # impacto de la columna PK referenciada: 1 relación, otro extremo = t2.c2
    imp = mod.get(f"/api/relationships/impact?columnId={c1}")
    s.eq("impact responde 200", imp.status, 200)
    data = imp.data or {}
    s.eq("total = 1 relación", data.get("total"), 1)
    rels = data.get("relationships") or []
    s.check("enriquecido con tabla.columna del otro extremo",
            bool(rels) and bool(rels[0].get("otherTableName")) and bool(rels[0].get("otherColumnName")), f"{rels[:1]}")
    s.check("lista los canvases donde la relación es visible",
            any(c.get("name") == cname for r0 in rels for c in (r0.get("canvases") or [])),
            f"canvases={[r0.get('canvases') for r0 in rels]}")
    # columna sin relaciones → impacto vacío
    s.eq("columna sin relaciones → total 0",
         (mod.get(f"/api/relationships/impact?columnId={c2}x-no-existe").data or {}).get("total"), 0)
    # overlay del draft: una relación agregada SOLO en el draft también cuenta
    cs = mod.draft(pid, f"{TAG} s14")
    t3, c3, nrid = _uid("tbl"), _uid("col"), _uid("rel")
    r = mod.bulk(cs, [
        {"collection": "canonical_tables", "entityId": t3, "op": "upsert",
         "payload": {"logicalName": f"{TAG} riesgo", "physicalName": phys(f"{TAG} riesgo"), "schema": "e2e"}},
        {"collection": "canonical_columns", "entityId": c3, "op": "upsert",
         "payload": {"tableId": t3, "logicalName": "id cliente", "physicalName": "ID_CLIENTE",
                     "dataType": "BIGINT", "ordinal": 0, "isForeignKey": True}},
        {"collection": "relationships", "entityId": nrid, "op": "upsert",
         "payload": {"parentTableId": t1, "childTableId": t3,
                     "pairs": [{"parentColumnId": c1, "childColumnId": c3}],
                     "parentCardinality": "one", "childCardinality": "zero-many"}}])
    s.eq("relación nueva en el draft", r.status, 200)
    s.eq("impact con overlay del draft = 2",
         (mod.get(f"/api/relationships/impact?columnId={c1}&changesetId={cs}").data or {}).get("total"), 2)
    s.eq("impact SIN draft sigue = 1 (aislamiento)",
         (mod.get(f"/api/relationships/impact?columnId={c1}").data or {}).get("total"), 1)
    return s


# ══ S15 · #1 Glosario: validación frase completa + lock ADMIN (D1/D4) ════════
def s15_glosario_validacion_lock() -> Suite:
    s = Suite("s15_glosario_validacion_lock")
    admin, mod, rev = Client("admin"), Client("modelador"), Client("revisor")
    marker = f"zx{uuid.uuid4().hex[:6]}"      # palabra única, imposible en otro corpus

    # corpus: columna publicada cuyo nombre lógico contiene la palabra marcador
    # (doc 94 D9: un término de columna sólo choca con nombres de columnas)
    pid = mod.create_project()
    seed = Seed(mod, pid)
    tid = seed.table(f"{TAG} prestamo")
    seed.column(tid, f"codigo {marker}", "STRING", physical="CODIGO_MARCADOR")
    seed.publish(rev)
    gl = f"/api/projects/{pid}/glossary"

    def apply_terms(**body):
        return admin.apply_standards(pid, {"kind": "glossary", **body})

    # 1) palabra que SÍ existe en nombres lógicos → conflicto
    r = admin.post(f"{gl}/validate", {"term": marker, "scope": "column"})
    conf = (r.data or {}).get("conflicts") or {}
    s.check("palabra existente en corpus → conflicto", r.status == 200 and (r.data or {}).get("ok") is False,
            f"total={conf.get('total')}")
    s.check("conflictos traen tabla/columna/nombre lógico",
            all(k in ((conf.get("corpus") or [{}])[0]) for k in ("tableName", "logicalName")),
            f"{(conf.get('corpus') or [None])[0]}")
    # 2) frase completa que NO existe → pasa (caso 'codigo de análisis' de la spec)
    r = admin.post(f"{gl}/validate", {"term": f"{marker} de analisis", "scope": "column"})
    s.check("frase completa inexistente → OK", (r.data or {}).get("ok") is True, f"{r.data}")
    # 3) substring que no es palabra completa NO conflictúa
    r = admin.post(f"{gl}/validate", {"term": marker[:4], "scope": "column"})
    s.check("substring parcial (no palabra completa) → OK", (r.data or {}).get("ok") is True,
            f"term={marker[:4]} {str(r.data)[:80]}")

    # 4) enforcement en el write (Data Standards): término en corpus → 409
    s.eq("apply con término en corpus → 409",
         apply_terms(termsUpsert=[{"term": marker, "abbrev": "ZZQ", "scope": "column"}]).status, 409)
    # Doc 105 (D1b): el CRUD directo del glosario ya no escribe
    r = admin.post(gl, {"term": f"{marker}d", "abbrev": "ZZD", "scope": "column"})
    s.check("alta DIRECTA del glosario → 409 (va por Data Standards)",
            r.status == 409 and r.detail == STANDARDS_VERSIONED, f"status={r.status}")
    # 5) término limpio se crea; duplicado exacto (case-insens) → 409
    clean = f"{marker}g"
    s.eq("término limpio se crea (apply)",
         apply_terms(termsUpsert=[{"term": clean, "abbrev": "ZZG", "scope": "column"}]).status, 200)
    gid = next((t["id"] for t in (admin.get(gl).data or []) if t["term"] == clean), None)
    s.check("el término quedó en el glosario", bool(gid))
    s.eq("duplicado exacto en glosario (case-insens) → 409",
         apply_terms(termsUpsert=[{"term": clean.upper(), "abbrev": "ZZH", "scope": "column"}]).status, 409)

    # 6) lock por ADMIN (D4): intocable para TODOS hasta unlock
    r = admin.post(f"{gl}/{gid}/lock")
    s.check("admin bloquea", r.status == 200 and (r.data or {}).get("locked") is True, f"{r.data}")
    s.eq("modelador NO puede bloquear (admin.manage) → 403", mod.post(f"{gl}/{gid}/lock").status, 403)
    s.eq("editar bloqueado → 409 incluso ADMIN",
         apply_terms(termsUpsert=[{"id": gid, "term": clean, "abbrev": "ZZ2", "scope": "column"}]).status, 409)
    s.eq("bloqueado con la abreviatura vacía → 409 (el bloqueo manda, doc 105 C1)",
         apply_terms(termsUpsert=[{"id": gid, "term": clean, "abbrev": "", "scope": "column"}]).status, 409)
    s.eq("eliminar bloqueado → 409", apply_terms(termsDelete=[gid]).status, 409)
    # unlock → editable de nuevo
    admin.post(f"{gl}/{gid}/unlock")
    s.eq("tras unlock se puede editar",
         apply_terms(termsUpsert=[{"id": gid, "term": clean, "abbrev": "ZZ4", "scope": "column"}]).status, 200)
    s.eq("una edición idéntica no es un cambio → 422 (doc 105 P1-bis)",
         apply_terms(termsUpsert=[{"id": gid, "term": clean, "abbrev": " ZZ4 ", "scope": "column"}]).status, 422)
    # auditoría del lock
    entries = admin.get("/api/admin/audit?limit=100").data or []
    acts = {e.get("action") for e in entries}
    s.check("lock/unlock auditados", {"glossary.lock", "glossary.unlock"} <= acts,
            f"{[a for a in acts if a and 'lock' in a]}")
    return s


# ══ S16 · #2 Parent Domain: impacto detallado con tablas + búsqueda ══════════
def s16_domain_impact() -> Suite:
    s = Suite("s16_domain_impact")
    admin, mod, rev = Client("admin"), Client("modelador"), Client("revisor")
    pid = mod.create_project()
    dom = admin.create_domain(pid, f"{TAG} Importe", "DECIMAL(18,2)")   # Data Standards (standards.edit)

    seed = Seed(mod, pid)
    t1 = seed.table(f"{TAG} saldos")
    t2 = seed.table(f"{TAG} movimientos")
    # 2 columnas SIGUEN el tipo del dominio; 1 con override manual
    seed.column(t1, "saldo actual", "DECIMAL(18,2)", domain=dom, ordinal=0)
    seed.column(t1, "saldo anterior", "DECIMAL(18,2)", domain=dom, ordinal=1)
    seed.column(t2, "importe", "STRING", domain=dom, ordinal=0, typeOverridden=True)
    seed.canvas(f"{TAG} dom-canvas", table_ids=[t1])
    seed.publish(rev)

    base = f"/api/projects/{pid}/domains/{dom}/impact"
    imp = mod.get(base)
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
    dq = mod.get(base, params={"q": name1}).data or {}
    tq = dq.get("tables") or []
    s.check("q filtra a la tabla buscada", len(tq) == 1 and tq[0].get("tableId") == t1,
            f"q={name1} → {[t.get('physicalName') for t in tq]}")
    s.eq("conteo global de columnas NO cambia con q", dq.get("columnsUsing"), 3)
    s.eq("totalTables con q = matches (pagina la lista filtrada)", dq.get("totalTables"), 1)
    # paginación
    s.eq("limit=1 devuelve 1 tabla", len((mod.get(base, params={"limit": 1}).data or {}).get("tables") or []), 1)
    # Doc 105 (D1b): propagate directo cerrado
    s.eq("propagate directo → 409", admin.post(f"/api/projects/{pid}/domains/{dom}/propagate").status, 409)
    return s


# ══ S17 · #3-5 Vistas: multi-fuente, membresía del canvas, User-Defined SQL ═══
def s17_vistas_multifuente() -> Suite:
    s = Suite("s17_vistas_multifuente")
    mod, rev = Client("modelador"), Client("revisor")
    pid = mod.create_project()
    seed = Seed(mod, pid)
    t1 = seed.table(f"{TAG} cliente")
    c1 = seed.column(t1, "id cliente", "BIGINT", pk=True, ordinal=0)
    seed.column(t1, "nombre cliente", "VARCHAR(120)", ordinal=1)
    t2 = seed.table(f"{TAG} cuenta")
    seed.column(t2, "id cuenta", "BIGINT", pk=True, ordinal=0)
    c2 = seed.column(t2, "id cliente", "BIGINT", ordinal=1, isForeignKey=True)
    seed.column(t2, "saldo actual", "DECIMAL(18,2)", ordinal=2)
    seed.relationship(t1, c1, t2, c2)
    ca = seed.canvas(f"{TAG} vw-canvas", table_ids=[t1, t2])
    seed.publish(rev)

    # 1) vista MULTI-FUENTE con columnas propias (alias + cast), miembro del canvas (doc 70)
    vid, vname = _uid("view"), phys(_uid("VW_CLIENTE_360"))
    body = {"name": vname, "schema": "e2e_vu", "sourceTableIds": [t1, t2],
            "sources": [{"tableId": t1, "column": "NOMBRE_CLIENTE", "outputAlias": "NOMBRE"},
                        {"tableId": t2, "column": "SALDO_ACTUAL", "castType": "DECIMAL(18,2)",
                         "outputAlias": "SALDO_TOTAL"}]}
    sa = mod.get(f"/api/subject-areas/{ca}").data
    cs = mod.draft(pid, f"{TAG} s17")
    s.eq("vista multi-fuente en el draft", mod.change(cs, "views", vid, body).status, 200)
    r = mod.change(cs, "subject_areas", ca, {**sa, "viewIds": [vid],
                                             "layout": {**sa["layout"], vid: {"x": 900, "y": 40}}})
    s.eq("la vista entra al canvas (viewIds)", r.status, 200)
    s.eq("publicar", mod.publish(cs, rev).status, 200)

    v = _by_id(mod.get(f"/api/views?projectId={pid}").data, vid) or {}
    s.eq("tableId legacy = primera fuente", v.get("tableId"), t1)
    s.eq("sourceTableIds completo", v.get("sourceTableIds"), [t1, t2])
    s.check("sources conservan castType/alias",
            any(x.get("castType") == "DECIMAL(18,2)" for x in v.get("sources") or []))
    # Doc 91 D5: sin joinOverride (el DDL lista `FROM t1, t2`); el campo no viaja.
    s.check("joinOverride ya no existe en el doc (doc 91 D5)", "joinOverride" not in v)

    # 2) GET ?tableId= matchea por CUALQUIER fuente (no solo la primera)
    for t in (t1, t2):
        s.check(f"listado por fuente {'principal' if t == t1 else 'secundaria'} la incluye",
                _by_id(mod.get(f"/api/views?tableId={t}").data, vid) is not None)

    # 3) diagrama: la vista es miembro del canvas y trae sus fuentes
    diag = mod.get(f"/api/subject-areas/{ca}/diagram").data or {}
    dview = _by_id(diag.get("views"), vid)
    s.check("diagrama incluye la vista (miembro del canvas)", dview is not None,
            f"views={[x.get('id') for x in diag.get('views') or []]}")
    s.check("payload de vista trae fuentes para derivaciones", set((dview or {}).get("sourceTableIds") or []) == {t1, t2})

    # 4) User-Defined SQL (doc 91 D6): se guarda VERBATIM aunque no parsee
    bad_sql = f"CREATE VIEW e2e_vu.{vname} AS SELEC nombre_cliente FRM e2e.cliente"
    cs = mod.draft(pid, f"{TAG} s17-sql")
    s.eq("User-Defined SQL inválido en el draft → 200",
         mod.change(cs, "views", vid, {**body, "customSql": bad_sql}).status, 200)
    s.eq("publicar el SQL", mod.publish(cs, rev).status, 200)
    s.eq("customSql round-trip verbatim",
         (_by_id(mod.get(f"/api/views?projectId={pid}").data, vid) or {}).get("customSql"), bad_sql)
    return s


# ══ S18 · #10 UDP de canvas (Modelo de Datos) + reporting `models` ═══════════
def s18_udp_canvas_models() -> Suite:
    s = Suite("s18_udp_canvas_models")
    admin, mod, rev, lector = Client("admin"), Client("modelador"), Client("revisor"), Client("lector")
    pid = mod.create_project()
    cname = f"{TAG} modelo-riesgos"
    seed = Seed(mod, pid)
    tid = seed.table(f"{TAG} m1")
    ca = seed.canvas(cname, table_ids=[tid])
    seed.publish(rev)

    # 1) definición UDP nivel canvas por Data Standards (versionada)
    key = f"{TAG} Criticidad"
    r = admin.apply_standards(pid, {"kind": "udp", "udpUpsert": [
        {"name": key, "level": "canvas", "dataType": "list",
         "allowedValues": ["alta", "media", "baja"], "defaultValue": "media"}]})
    s.eq("definición UDP level=canvas aplicada", r.status, 200)
    cdef = next((x for x in (admin.get(f"/api/projects/{pid}/udp").data or []) if x["name"] == key), None)
    s.check("definición visible con level=canvas", bool(cdef) and cdef["level"] == "canvas", f"{cdef}")
    kid = (cdef or {}).get("id", "x")

    # 2) asignación al canvas POR VERSIÓN (doc 105: el PUT directo /udp ya no existe)
    sa = mod.get(f"/api/subject-areas/{ca}").data
    cs = mod.draft(pid, f"{TAG} s18")
    s.eq("modelador asigna UDP al canvas en el draft",
         mod.change(cs, "subject_areas", ca, {**sa, "udpValues": {kid: "alta"}}).status, 200)
    s.eq("lector NO puede abrir una versión (model.edit) → 403",
         lector.post("/api/changesets/snapshot", {"projectId": pid}).status, 403)
    s.eq("publicar la asignación", mod.publish(cs, rev).status, 200)
    got = _by_id(mod.get(f"/api/projects/{pid}/subject-areas").data, ca) or {}
    s.eq("round-trip udpValues del canvas", (got.get("udpValues") or {}).get(kid), "alta")

    # 3) reporting: entidad `models` consulta el UDP de canvas
    q = {"projectId": pid, "from": "models", "select": ["name", f"udp.{kid}"],
         "where": {"op": "and", "conditions": [{"field": f"udp.{kid}", "op": "eq", "value": "alta"}]},
         "limit": 50}
    r = mod.post("/api/reporting/query", q)
    rows = (r.data or {}).get("rows") or []
    s.check("query models filtra por udp de canvas", r.status == 200 and any(x.get("name") == cname for x in rows),
            f"status={r.status} rows={len(rows)} {str(r.raw)[:120]}")
    # 4) catálogo: models presente; tableCount derived (ops vacíos)
    cat = mod.get(f"/api/reporting/catalog?projectId={pid}&from=models").data or {}
    tc = next((f for f in cat.get("fields") or [] if f.get("key") == "tableCount"), None)
    s.check("catálogo expone models.tableCount derived sin ops",
            bool(tc) and tc.get("derived") is True and not tc.get("ops"), f"{tc}")
    # 5) filtrar por derived → 422 (backstop del motor)
    bad = {"projectId": pid, "from": "models", "select": ["name"],
           "where": {"op": "and", "conditions": [{"field": "tableCount", "op": "gt", "value": 1}]}}
    s.eq("filtro sobre derived → 422", mod.post("/api/reporting/query", bad).status, 422)
    # 6) insight de cobertura incluye el nivel canvas
    cov = admin.get(f"/api/reporting/insights/udp-coverage?projectId={pid}").data or []
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
    mod, otro, rev = Client("modelador"), Client("modelador2"), Client("revisor")

    pid = mod.create_project()
    seed = Seed(mod, pid)
    tid = seed.table(f"{TAG} bulk base")
    c1 = seed.column(tid, "id", "BIGINT", pk=True, ordinal=0)
    c2 = seed.column(tid, "saldo", "STRING", ordinal=1)
    seed.publish(rev)
    cs = mod.draft(pid, f"{TAG} s19")

    # 1) crear tabla nueva + 3 columnas en UN lote (forma de "New table from sources")
    nid = _uid("tbulk")
    cols = [_uid("col") for _ in range(3)]
    r = mod.bulk(cs, [
        {"collection": "canonical_tables", "entityId": nid, "op": "upsert",
         "payload": {"logicalName": f"{TAG} bulk nueva", "physicalName": f"{TAG}_BULK_NUEVA".upper(), "schema": "e2e"}},
        *[{"collection": "canonical_columns", "entityId": cid, "op": "upsert",
           "payload": {"tableId": nid, "logicalName": f"col {i}", "physicalName": f"C{i}",
                       "dataType": "STRING", "ordinal": i}}
          for i, cid in enumerate(cols)],
    ])
    s.eq("lote tabla+3 columnas → 200", r.status, 200)
    eff = mod.get(f"/api/changesets/{cs}/effective/canonical_columns?tableId={nid}").data or []
    s.eq("effective muestra las 3 columnas del lote", len(eff), 3)

    # 2) dup INTRA-lote → 409 y NADA del lote queda grabado (validación previa)
    nid2 = _uid("tbulk2")
    r = mod.bulk(cs, [
        {"collection": "canonical_tables", "entityId": nid2, "op": "upsert",
         "payload": {"logicalName": f"{TAG} bulk dup", "physicalName": f"{TAG}_BULK_DUP".upper(), "schema": "e2e"}},
        {"collection": "canonical_columns", "entityId": f"{nid2}.a", "op": "upsert",
         "payload": {"tableId": nid2, "logicalName": "a", "physicalName": "REPETIDA", "dataType": "STRING", "ordinal": 0}},
        {"collection": "canonical_columns", "entityId": f"{nid2}.b", "op": "upsert",
         "payload": {"tableId": nid2, "logicalName": "b", "physicalName": "repetida", "dataType": "STRING", "ordinal": 1}},
    ])
    s.eq("dup intra-lote → 409", r.status, 409)
    eff = mod.get(f"/api/changesets/{cs}/effective/canonical_tables").data or []
    s.check("el 409 no grabó NADA del lote (tabla dup ausente)", _by_id(eff, nid2) is None)

    # 3) ítem inválido → 422 sin escribir; colección fuera de whitelist → 422
    r = mod.bulk(cs, [{"collection": "canonical_columns", "entityId": _uid("bad"), "op": "upsert",
                       "payload": {"logicalName": "sin tableId"}}])
    s.eq("payload inválido en el lote → 422", r.status, 422)
    s.eq("colección no versionada → 422",
         mod.bulk(cs, [{"collection": "users", "entityId": "x", "op": "delete"}]).status, 422)

    # 4) sólo el OWNER escribe su working copy → 403
    s.eq("bulk de un no-owner → 403",
         otro.bulk(cs, [{"collection": "canonical_columns", "entityId": c1, "op": "delete"}]).status, 403)

    # 5) cascada de borrado de la tabla PUBLICADA (columnas + tabla) en un lote:
    #    effective deja de mostrarla; producción la sigue viendo (draft aislado)
    r = mod.bulk(cs, [
        {"collection": "canonical_columns", "entityId": c1, "op": "delete"},
        {"collection": "canonical_columns", "entityId": c2, "op": "delete"},
        {"collection": "canonical_tables", "entityId": tid, "op": "delete"},
    ])
    s.eq("lote de deletes (cascada) → 200", r.status, 200)
    eff = mod.get(f"/api/changesets/{cs}/effective/canonical_tables").data or []
    s.check("effective ya no muestra la tabla borrada", _by_id(eff, tid) is None)
    s.check("producción la sigue mostrando (pre-publish)", mod.table(pid, tid) is not None)
    return s


# ══ S20 · Relaciones con llave compuesta (doc 47) ════════════════════════════
def s20_composite_key_relationships() -> Suite:
    """N=N (doc 47): un upsert de relación migra la llave COMPLETA del padre —
    parcial → 409; completo (con columnas del mismo lote) → 200."""
    s = Suite("s20_composite_key_relationships")
    mod, rev = Client("modelador"), Client("revisor")

    pid = mod.create_project()
    seed = Seed(mod, pid)
    ptid = seed.table(f"{TAG} rel padre")
    pk1 = seed.column(ptid, "id", "BIGINT", pk=True, ordinal=0)
    pk2 = seed.column(ptid, "region", "STRING", pk=True, ordinal=1)
    htid = seed.table(f"{TAG} rel hijo")
    seed.publish(rev)
    cs = mod.draft(pid, f"{TAG} s20")

    rid, na, nb = _uid("rel"), _uid("colA"), _uid("colB")
    rel = {"parentTableId": ptid, "childTableId": htid, "parentCardinality": "one",
           "childCardinality": "zero-many", "identifying": True}
    parcial = mod.bulk(cs, [{"collection": "relationships", "entityId": rid, "op": "upsert",
                             "payload": {**rel, "pairs": [{"parentColumnId": pk1, "childColumnId": na}]}}])
    s.eq("relación con 1 par sobre llave de 2 → 409", parcial.status, 409)

    completo = mod.bulk(cs, [
        {"collection": "canonical_columns", "entityId": na, "op": "upsert",
         "payload": {"tableId": htid, "logicalName": "id", "physicalName": "FK_ID", "dataType": "BIGINT",
                     "ordinal": 0, "isForeignKey": True, "isPrimaryKey": True, "isNullable": False}},
        {"collection": "canonical_columns", "entityId": nb, "op": "upsert",
         "payload": {"tableId": htid, "logicalName": "region", "physicalName": "FK_REGION", "dataType": "STRING",
                     "ordinal": 1, "isForeignKey": True, "isPrimaryKey": True, "isNullable": False}},
        {"collection": "relationships", "entityId": rid, "op": "upsert",
         "payload": {**rel, "pairs": [{"parentColumnId": pk1, "childColumnId": na},
                                      {"parentColumnId": pk2, "childColumnId": nb}]}},
    ])
    s.eq("llave completa + columnas del lote → 200", completo.status, 200)
    eff = mod.get(f"/api/changesets/{cs}/effective/relationships?tableId={ptid}").data or []
    s.eq("effective trae la relación con 2 pares", len((eff[0] or {}).get("pairs", [])) if eff else 0, 2)
    return s


# ══ S21 · Carga masiva desde Excel (docs 55/78) ══════════════════════════════
def s21_bulk_upload() -> Suite:
    """Doc 78: perfil de carga por proyecto (built-in «Plantilla BCP») +
    Validate → reporte → apply dentro del draft, con polling del job: crea
    subject/canvas/esquema/tablas/columnas; un job ajeno es 403; un reporte con
    errores no se aplica (409); la re-carga idéntica es unchanged."""
    s = Suite("s21_bulk_upload")
    mod, otro = Client("modelador"), Client("modelador2")
    pid = mod.create_project(name=f"{TAG} proyecto carga")
    cs = mod.draft(pid, f"{TAG} s21")
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
    s.eq("job de otro usuario → 403", otro.get(f"/api/changesets/{cs}/uploads/{job.get('id')}").status, 403)

    # 2) apply async → escribe en el draft; el canvas afectado vuelve en el resultado
    r = mod.post(f"/api/changesets/{cs}/uploads/{job.get('id')}/apply")
    s.eq("apply → 202", r.status, 202)
    done = (wait(job.get("id")).data or {})
    s.eq("apply termina en applied", done.get("status"), "applied")
    s.eq("1 canvas afectado", len((done.get("result") or {}).get("affectedCanvasIds") or []), 1)
    eff = mod.get(f"/api/changesets/{cs}/effective/canonical_tables?q={p_uno[:-4]}").data or []
    s.eq("effective muestra las 2 tablas nuevas", len([t for t in eff if t["physicalName"] in (p_uno, p_dos)]), 2)
    tid = next((t["id"] for t in eff if t["physicalName"] == p_uno), None)
    cols = mod.get(f"/api/changesets/{cs}/effective/canonical_columns?tableId={tid}").data or []
    s.eq("la tabla uno tiene sus 2 columnas", len(cols), 2)
    s.check("la PK quedó marcada y primera en el orden único (doc 94)",
            any(c.get("isPrimaryKey") and c.get("ordinal") == 0 for c in cols))
    s.check("producción NO ve las tablas (draft aislado)",
            not any(t["physicalName"] == p_uno for t in mod.tables(pid)))

    # 3) tipo inválido → reporte con error → apply 409
    r = mod.post(f"/api/changesets/{cs}/uploads", workbook(tipo_nombre="texto raro"))
    bad = (wait(r.data["id"]).data or {})
    s.eq("reporte con 1 error (invalid-type)", (bad.get("report") or {}).get("errorCount"), 1)
    s.eq("apply con errores → 409", mod.post(f"/api/changesets/{cs}/uploads/{bad.get('id')}/apply").status, 409)

    # 4) re-carga idéntica → todo unchanged, sin cambios nuevos
    r = mod.post(f"/api/changesets/{cs}/uploads", workbook())
    again = (wait(r.data["id"]).data or {})
    summary = (again.get("report") or {}).get("summary", {})
    s.eq("re-carga idéntica: 2 tablas unchanged", summary.get("tables", {}).get("unchanged"), 2)
    s.eq("re-carga idéntica: 2 columnas unchanged", summary.get("columns", {}).get("unchanged"), 2)
    s.eq("discard del job → 200", mod.delete(f"/api/changesets/{cs}/uploads/{again.get('id')}").status, 200)
    return s


# ═════════════════════════════════════════════════════════════════════════════
# Doc 105: flujos de las suites standalone (e2e_*.py), ahora escenarios del runner
# ═════════════════════════════════════════════════════════════════════════════

def _rollback_to(owner: Client, reviewer: Client, version_id: str) -> tuple[int, dict]:
    """Doc 27: restaura el proyecto A `version_id` con un draft inverso y lo
    publica. Devuelve (status del rollback, cabecera del draft)."""
    r = owner.post(f"/api/changesets/{version_id}/rollback")
    if r.status != 200:
        return r.status, r.data or {}
    pub = owner.publish(r.data["id"], reviewer, f"{TAG} rollback")
    return pub.status, r.data


def _published(cli: Client, pid: str) -> str:
    return (cli.get(f"/api/projects/{pid}/versions/published").data or {}).get("id")


# ══ S22 · Esquemas versionados (doc 18) ══════════════════════════════════════
def s22_schemas() -> Suite:
    s = Suite("s22_schemas")
    mod, rev = Client("modelador"), Client("revisor")
    pid = mod.create_project()
    seed = Seed(mod, pid)
    seed.table(f"{TAG} en uso", schema="e2e_uso")
    seed.publish(rev)
    prev = _published(mod, pid)

    def prod_names():
        return {x["name"] for x in (mod.get(f"/api/projects/{pid}/schemas").data or [])}

    in_use = next((x for x in (mod.get(f"/api/projects/{pid}/schemas").data or []) if x["name"] == "e2e_uso"), {})
    name, sid = f"e2e_sch_{uuid.uuid4().hex[:6]}", _uid("sch")
    renamed = f"{name}_v2"
    cs = mod.draft(pid, f"{TAG} s22")
    s.eq("crear esquema en el draft", mod.change(cs, "schemas", sid, {"name": name, "description": "e2e"}).status, 200)
    eff = mod.get(f"/api/changesets/{cs}/effective/schemas").data or []
    s.check("effective muestra el esquema del draft", any(x["name"] == name for x in eff))
    s.check("producción NO lo muestra antes del publish", name not in prod_names())
    s.eq("duplicado case-insensitive → 409", mod.change(cs, "schemas", _uid("sch"), {"name": name.upper()}).status, 409)
    r = mod.post(f"/api/changesets/{cs}/schemas/{sid}/rename", {"newName": renamed})
    s.check("rename en el draft (propagación 0/0)", r.status == 200 and r.data == {"tables": 0, "views": 0},
            f"{r.status} {r.data}")
    s.eq("delete de esquema en uso → 409",
         mod.post(f"/api/changesets/{cs}/schemas/{in_use.get('id')}/delete").status, 409)
    s.eq("publicar", mod.publish(cs, rev).status, 200)
    s.check("publicado: producción muestra el esquema renombrado", renamed in prod_names() and name not in prod_names())
    st, _ = _rollback_to(mod, rev, prev)
    s.eq("rollback a la versión previa publicado", st, 200)
    s.check("tras el rollback el esquema ya no está en producción", not {name, renamed} & prod_names())
    return s


# ══ S23 · Relaciones v2 (doc 19): compuesta, payload legacy, toggle, impact, rollback ═
def s23_relationships_v2() -> Suite:
    s = Suite("s23_relationships_v2")
    mod, rev = Client("modelador"), Client("revisor")
    pid = mod.create_project()
    seed = Seed(mod, pid)
    tp = seed.table(f"{TAG} rel padre")
    p1 = seed.column(tp, "cod a", pk=True, ordinal=0)
    p2 = seed.column(tp, "cod b", pk=True, ordinal=1)
    ts = seed.table(f"{TAG} rel simple")
    ps = seed.column(ts, "cod s", pk=True, ordinal=0)
    tc = seed.table(f"{TAG} rel hijo")
    c1 = seed.column(tc, "cod a", ordinal=0, isForeignKey=True)
    c2 = seed.column(tc, "cod b", ordinal=1, isForeignKey=True)
    seed.publish(rev)
    prev = _published(mod, pid)

    def eff_rels(cs, tid):
        return mod.get(f"/api/changesets/{cs}/effective/relationships?tableId={tid}").data or []

    def prod_rels(tid):
        return mod.get(f"/api/relationships?tableId={tid}").data or []

    # 1) relación COMPUESTA identifying (2 pares, roleName) en el draft
    cs = mod.draft(pid, f"{TAG} s23")
    rid = _uid("rel")
    r = mod.change(cs, "relationships", rid, {
        "parentTableId": tp, "childTableId": tc,
        "pairs": [{"parentColumnId": p1, "childColumnId": c1},
                  {"parentColumnId": p2, "childColumnId": c2, "roleName": "rol_b"}],
        "parentCardinality": "one", "childCardinality": "zero-many", "identifying": True})
    s.eq("crear relación compuesta v2 en el draft", r.status, 200)
    mine = _by_id(eff_rels(cs, tp), rid)
    s.check("effective la muestra en shape v2 (2 pares + roleName)",
            bool(mine) and len(mine.get("pairs", [])) == 2 and mine["pairs"][1].get("roleName") == "rol_b"
            and mine.get("identifying") is True, str(mine)[:200])
    s.check("producción NO la muestra antes del publish", _by_id(prod_rels(tp), rid) is None)

    # 2) payload LEGACY (source/target) → add_change lo normaliza a v2
    rid_leg = _uid("rel")
    r = mod.change(cs, "relationships", rid_leg, {
        "sourceTableId": tc, "sourceColumnId": c1, "targetTableId": ts, "targetColumnId": ps,
        "sourceCardinality": "many", "targetCardinality": "zero-many", "identifying": False})
    leg = _by_id(eff_rels(cs, ts), rid_leg)
    s.check("payload legacy queda normalizado (hijo=source, child zero-many)",
            r.status == 200 and bool(leg) and leg.get("childTableId") == tc and leg.get("parentTableId") == ts
            and leg.get("childCardinality") == "zero-many" and (leg.get("pairs") or [{}])[0].get("childColumnId") == c1,
            f"{r.status} {str(leg)[:200]}")
    s.eq("quitar la legacy del draft", mod.change(cs, "relationships", rid_leg, None, op="delete").status, 200)

    # 3) toggle a non-identifying (upsert del doc completo) y publish
    s.eq("toggle a non-identifying en el draft",
         mod.change(cs, "relationships", rid, {**(mine or {}), "identifying": False}).status, 200)
    s.eq("publicar", mod.publish(cs, rev).status, 200)
    pub = _by_id(prod_rels(tp), rid)
    s.check("publicado: producción v2 con 2 pares y non-identifying",
            bool(pub) and len(pub.get("pairs", [])) == 2 and pub.get("identifying") is False, str(pub)[:200])

    # 4) impact por la columna de un par (extremo padre)
    imp = mod.get(f"/api/relationships/impact?columnId={p2}").data or {}
    row = next((x for x in imp.get("relationships", []) if x.get("relId") == rid), None)
    s.check("impact por columna del par: thisSide=parent y otherColumn=hija",
            bool(row) and row.get("thisSide") == "parent" and row.get("otherColumnId") == c2, str(row)[:200])

    # 5) rollback → desaparece de producción
    st, _ = _rollback_to(mod, rev, prev)
    s.eq("rollback a la versión previa publicado", st, 200)
    s.check("tras el rollback la relación desaparece de producción", _by_id(prod_rels(tp), rid) is None)
    return s


# ══ S24 · Rollback de versiones (doc 27) ═════════════════════════════════════
def s24_rollback() -> Suite:
    s = Suite("s24_rollback")
    mod, rev = Client("modelador"), Client("revisor")
    pid = mod.create_project()
    seed = Seed(mod, pid)
    tid = seed.table(f"{TAG} rollback base", description="original")
    seed.publish(rev)
    prev1 = _published(mod, pid)

    # 0) restaurar A la versión vigente: no hay nada posterior que deshacer → 409
    r = mod.post(f"/api/changesets/{prev1}/rollback")
    s.check("rollback de la versión vigente → 409 (nada que deshacer)",
            r.status == 409 and "current production" in str(r.detail), f"{r.status} {r.detail}")

    # 1) publicar: tabla EXISTENTE modificada + tabla NUEVA
    tdoc = mod.table(pid, tid) or {}
    new_id = _uid("tbl")
    cs1 = mod.draft(pid, f"{TAG} cambio a revertir")
    mod.change(cs1, "canonical_tables", tid, {**tdoc, "description": "DESC MODIFICADA POR E2E"})
    mod.change(cs1, "canonical_tables", new_id, {"physicalName": phys(f"{TAG} nueva rollback"),
                                                 "logicalName": f"{TAG} nueva rollback", "schema": "e2e"})
    s.eq("publicar el cambio", mod.publish(cs1, rev).status, 200)
    s.eq("cambio publicado", (mod.table(pid, tid) or {}).get("description"), "DESC MODIFICADA POR E2E")

    # 2) rollback A la versión previa ⇒ draft inverso, aprobado por el flujo normal
    st, draft = _rollback_to(mod, rev, prev1)
    s.check("el rollback nace como draft «Restore to …»",
            draft.get("status") == "draft" and str(draft.get("title", "")).startswith("Restore to"), str(draft)[:160])
    s.eq("rollback aprobado/publicado", st, 200)

    # 3) producción restaurada
    s.eq("descripción restaurada al original", (mod.table(pid, tid) or {}).get("description"), "original")
    s.check("tabla creada en la versión → eliminada por el rollback", mod.table(pid, new_id) is None)

    # 4) la versión VIGENTE (el rollback recién publicado) no tiene nada que deshacer → 409
    s.eq("rollback de la versión vigente tras publicar → 409",
         mod.post(f"/api/changesets/{_published(mod, pid)}/rollback").status, 409)
    return s


# ══ S25 · Vistas versionadas (doc 20) ════════════════════════════════════════
def s25_views_versionadas() -> Suite:
    s = Suite("s25_views_versionadas")
    mod, rev = Client("modelador"), Client("revisor")
    pid = mod.create_project()
    seed = Seed(mod, pid)
    tid = seed.table(f"{TAG} fuente vista")
    seed.column(tid, "tipo estado", physical="TIPESTADO")
    seed.publish(rev)
    prev = _published(mod, pid)

    def prod_view():
        return _by_id(mod.get(f"/api/views?tableId={tid}").data, vid)

    vid = _uid("view")
    doc = {"name": phys(_uid("E2E_VU")), "schema": "e2e_vu", "sql": "", "tableId": tid, "sourceTableIds": [tid],
           "sources": [{"tableId": tid, "column": "TIPESTADO", "outputAlias": "TIPESTADO"}],
           "customSql": None, "description": "e2e"}
    cs = mod.draft(pid, f"{TAG} s25")
    s.eq("crear vista en el draft", mod.change(cs, "views", vid, doc).status, 200)
    s.check("effective la muestra",
            _by_id(mod.get(f"/api/changesets/{cs}/effective/views?tableId={tid}").data, vid) is not None)
    s.check("producción NO la muestra antes del publish", prod_view() is None)
    s.eq("editar la vista en el draft", mod.change(cs, "views", vid, {**doc, "description": "e2e v2"}).status, 200)
    s.eq("publicar", mod.publish(cs, rev).status, 200)
    s.eq("publicado: producción la muestra (con la edición)", (prod_view() or {}).get("description"), "e2e v2")
    st, _ = _rollback_to(mod, rev, prev)
    s.eq("rollback a la versión previa publicado", st, 200)
    s.check("tras el rollback la vista desaparece de producción", prod_view() is None)
    return s


# ══ S26 · Estructura versionada (doc 16): carpeta + canvas + tabla en el draft ═
def s26_estructura_versionada() -> Suite:
    s = Suite("s26_estructura_versionada")
    mod, rev = Client("modelador"), Client("revisor")
    pid = mod.create_project()                       # doc 75 D5: el proyecto nace directo
    fid, said, tid, cid = _uid("fld"), _uid("sa"), _uid("tbl"), _uid("col")
    tb = f"TB_E2E_DRAFT_{uuid.uuid4().hex[:6].upper()}"
    cs = mod.draft(pid, f"{TAG} s26")
    r = mod.bulk(cs, [
        {"collection": "schemas", "entityId": _uid("sch"), "op": "upsert", "payload": {"name": "e2e_schema"}},
        {"collection": "folders", "entityId": fid, "op": "upsert",
         "payload": {"projectId": pid, "parentFolderId": None, "name": "Dominio E2E", "order": 0}},
        {"collection": "canonical_tables", "entityId": tid, "op": "upsert",
         "payload": {"physicalName": tb, "logicalName": tb.lower().replace("_", " "), "schema": "e2e_schema"}},
        {"collection": "canonical_columns", "entityId": cid, "op": "upsert",
         "payload": {"tableId": tid, "physicalName": "CODE2E", "logicalName": "Codigo E2E", "dataType": "INT",
                     "ordinal": 0}},
        {"collection": "subject_areas", "entityId": said, "op": "upsert",
         "payload": {"projectId": pid, "folderId": fid, "name": "Canvas E2E", "tableIds": [tid], "viewIds": [],
                     "layout": {tid: {"x": 80, "y": 80}}, "drawings": [], "udpValues": {}}},
    ])
    s.eq("registrar la estructura en el draft (un lote)", r.status, 200)

    # producción NO la ve
    s.eq("producción: diagrama del canvas draft → 404", mod.get(f"/api/subject-areas/{said}/diagram").status, 404)
    s.check("producción: tabla draft INVISIBLE en catálogo", not mod.tables(pid, q=tb))
    # el estado EFECTIVO del draft sí
    dia = mod.get(f"/api/subject-areas/{said}/diagram?changesetId={cs}")
    d = dia.data or {}
    s.check("efectivo: el diagrama del draft renderiza",
            dia.status == 200 and len(d.get("tables") or []) == 1 and d["tables"][0].get("physicalName") == tb
            and (d.get("subjectArea") or {}).get("layout", {}).get(tid) is not None, f"status={dia.status}")
    # publicar → producción sí
    s.eq("publicar", mod.publish(cs, rev).status, 200)
    dia = mod.get(f"/api/subject-areas/{said}/diagram")
    s.check("publicado: diagrama renderiza SIN changeset", dia.status == 200 and len((dia.data or {}).get("tables") or []) == 1)
    s.check("publicado: carpeta visible", _by_id(mod.get(f"/api/projects/{pid}/folders").data, fid) is not None)
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
    # Doc 105: flujos de las antiguas suites standalone (e2e_*.py)
    "s22_schemas": s22_schemas,
    "s23_relationships_v2": s23_relationships_v2,
    "s24_rollback": s24_rollback,
    "s25_views_versionadas": s25_views_versionadas,
    "s26_estructura_versionada": s26_estructura_versionada,
}
