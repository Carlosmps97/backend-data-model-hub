"""Tests del Migrator multi-archivo (doc 32b) contra una BD FAKE en memoria:
adopción por clave natural (R1), conflicto por score (R2), alias de duplicados
internos (R3), dedup de relaciones (R4), A4 (defs UDP sin uso) y fusión de
folders/canvases homónimos (R8); doc 75: todo ocurre DENTRO de un proyecto
(ids namespaceados, prefetch acotado, estándares por proyecto). Cubre la
superficie real que usa el script: find / find_one / bulk_write(UpdateOne)."""
from __future__ import annotations

import pytest

from scripts.erwin_migration import erwin_parser as ep
from scripts.erwin_migration import policies as pol
from scripts.erwin_migration.migrate import Migrator


# ── BD fake (superficie mínima: find / find_one / bulk_write) ─────────────
def _match(doc: dict, flt: dict) -> bool:
    for k, cond in (flt or {}).items():
        val = doc.get(k)
        if isinstance(cond, dict):
            if "$ne" in cond and val == cond["$ne"]:
                return False
            if "$in" in cond and val not in cond["$in"]:
                return False
            if "$exists" in cond and (k in doc) != bool(cond["$exists"]):
                return False
        elif val != cond:
            return False
    return True


class FakeColl:
    def __init__(self, docs: dict):
        self.docs = docs

    def find(self, flt=None, projection=None):
        return [dict(d) for d in list(self.docs.values()) if _match(d, flt or {})]

    def find_one(self, flt=None, projection=None):
        for d in self.docs.values():
            if _match(d, flt or {}):
                return dict(d)
        return None

    def bulk_write(self, ops):
        for op in ops:
            _id = op._filter["_id"]
            doc = self.docs.get(_id)
            if doc is None:
                if not getattr(op, "_upsert", False):
                    continue
                doc = {"_id": _id, **(op._doc.get("$setOnInsert") or {})}
                self.docs[_id] = doc
            doc.update(op._doc.get("$set") or {})


class FakeDb:
    def __init__(self, data: dict[str, dict] | None = None):
        self.data: dict[str, dict] = data or {}

    def __getitem__(self, name: str) -> FakeColl:
        return FakeColl(self.data.setdefault(name, {}))

    def __getattr__(self, name: str) -> FakeColl:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


# ── constructores del modelo Erwin en memoria ─────────────────────────────
def attr(aid, owner, phys, order, *, kind="Entity", parent_attr=None,
         parent_rel=None, definition="", domain=None, dtype="STRING"):
    return ep.ErwinAttribute(
        id=aid, owner_id=owner, owner_kind=kind, name=phys.lower(),
        physical=phys, physical_raw=phys, data_type=dtype, logical_type="",
        nullable=True, order=order, definition=definition, comment="",
        domain_ref=domain, parent_attr_ref=parent_attr, parent_rel_ref=parent_rel)


def entity(eid, phys, attrs, pk=()):
    return ep.ErwinEntity(id=eid, name=phys.lower(), physical=phys,
                          physical_was_macro=False, definition="", comment="",
                          attributes=attrs, pk_attr_ids=set(pk),
                          pk_attr_order=list(pk))


def rel(rid, parent, child, rel_type=ep.REL_NON_IDENTIFYING):
    return ep.ErwinRelationship(id=rid, name=rid, rel_type=rel_type,
                                cardinality="-3", parent_ref=parent,
                                child_ref=child, null_option="100")


def base_db() -> FakeDb:
    """BD viva: TAB_A y TAB_C existen (de un archivo anterior) y están MÁS
    usadas que las copias del archivo 2 (2 relaciones, 2 canvases y una vista
    para TAB_A → score 7; la copia entrante suma 6) — deben ADOPTARSE."""
    P = {"projectId": "proj-1"}          # doc 75: todo doc de alcance lleva su proyecto
    return FakeDb({
        "canonical_tables": {
            "tbl-1": {"_id": "tbl-1", "schema": "S1", "physicalName": "TAB_A", **P},
            "tbl-3": {"_id": "tbl-3", "schema": "S1", "physicalName": "TAB_C", **P},
        },
        "canonical_columns": {
            "col-1": {"_id": "col-1", "tableId": "tbl-1", "physicalName": "CODA",
                      "erwinLongId": "OLD-A1", **P},
            "col-2": {"_id": "col-2", "tableId": "tbl-1", "physicalName": "NOMA",
                      "erwinLongId": "OLD-A2", **P},
            "col-5": {"_id": "col-5", "tableId": "tbl-3", "physicalName": "CODC",
                      "erwinLongId": "OLD-C1", **P},
        },
        "relationships": {
            "rel-1": {"_id": "rel-1", "parentTableId": "tbl-1", "childTableId": "tbl-3",
                      "pairs": [{"parentColumnId": "col-1", "childColumnId": "col-5"}],
                      "identifying": False, **P},
            "rel-2": {"_id": "rel-2", "parentTableId": "tbl-1", "childTableId": "tbl-3",
                      "pairs": [{"parentColumnId": "col-2", "childColumnId": "col-5"}],
                      "identifying": False, **P},
        },
        "views": {
            "vw-0": {"_id": "vw-0", "schema": "S1V", "name": "OTRA_VU",
                     "sourceTableIds": ["tbl-1"], **P},
        },
        "subject_areas": {
            "cv-1": {"_id": "cv-1", "name": "DIAG", "folderId": "fold-1",
                     "tableIds": ["tbl-1", "tbl-3"], **P,
                     "layout": {"tbl-1": {"x": 11, "y": 22}, "tbl-3": {"x": 33, "y": 44}}},
            "cv-2": {"_id": "cv-2", "name": "DIAG2", "folderId": "fold-1",
                     "tableIds": ["tbl-1"], "layout": {}, **P},
        },
        "folders": {
            "fold-1": {"_id": "fold-1", "name": "AREA", **P},
        },
        "projects": {"proj-1": {"_id": "proj-1", "name": "Familia DDV"}},
        "schemas": {"sch-S1": {"_id": "sch-S1", "name": "S1", **P}},
    })


def modelo_archivo_2() -> ep.ErwinModel:
    """Archivo 2 de la familia: repite TAB_A (idéntica, menos usada → se
    ADOPTA) y TAB_C (ídem), trae TAB_B nueva colgada de TAB_A, re-declara la
    FK TAB_A→TAB_C que YA existe (→ dedup R4), trae una copia duplicada de
    TAB_A sin uso (→ alias R3), una vista espejo de TAB_A y una def UDP
    basura sin uso (→ A4)."""
    m = ep.ErwinModel(name="Modelo de Datos DDV_FISICO")
    e1 = entity("E1", "TAB_A", [attr("A1", "E1", "CODA", 1),
                                attr("A2", "E1", "NOMA", 2)], pk=("A1",))
    e2 = entity("E2", "TAB_B", [
        attr("B1", "E2", "CODB", 1),
        attr("B2", "E2", "CODA", 2, parent_attr="A1", parent_rel="R1")], pk=("B1",))
    e3 = entity("E3", "TAB_C", [
        attr("C1", "E3", "CODC", 1, parent_attr="A1", parent_rel="R3")])
    e4 = entity("E4", "TAB_A", [attr("A1B", "E4", "CODA", 1)])   # copia interna
    m.entities = {e.id: e for e in (e1, e2, e3, e4)}
    m.relationships = {
        "R1": rel("R1", "E1", "E2"),
        "R3": rel("R3", "E1", "E3"),
        "RV1": rel("RV1", "E1", "V1", rel_type=ep.REL_TABLE_TO_VIEW),
    }
    v1 = ep.ErwinView(id="V1", name="TAB_A_VU", definition="", comment="",
                      attributes=[attr("VA1", "V1", "CODA", 1, kind="View",
                                       parent_attr="A1", parent_rel="RV1")])
    m.views = {"V1": v1}
    m.hive_dbs = {"S1": ["E1", "E2", "E3", "E4"], "S1V": ["V1"]}
    m.udp_defs = {"U1": ep.ErwinUdpDef(
        id="U1", full_name="Attribute.Physical.Basura", owner_class="Attribute",
        view_mode="Physical", short_name="Basura", data_type_code="6",
        default="", allowed_values=["X"])}
    m.subject_areas = [{"id": "SAX", "name": "AREA", "definition": "", "order": 0}]
    m.diagrams = [ep.ErwinDiagram(id="DGX", name="DIAG", subject_area="AREA",
                                  owner_path="Modelo.AREA",
                                  shapes=[("E1", None), ("E4", None), ("E2", None), ("V1", None)])]
    return m


@pytest.fixture
def corrida():
    db = base_db()
    mig = Migrator(db, modelo_archivo_2(), "Familia DDV", None)
    mig.run()
    return db, mig


# ── R1 · adopción ─────────────────────────────────────────────────────────
def test_adopcion_no_pisa_la_tabla_viva(corrida):
    db, mig = corrida
    # TAB_A y TAB_C se adoptaron: el doc vivo quedó tal cual (sin re-escribir)
    assert "migratedFrom" not in db.data["canonical_tables"]["tbl-1"]
    assert "migratedFrom" not in db.data["canonical_tables"]["tbl-3"]
    assert mig.table_pid["E1"] == "tbl-1" and mig.table_pid["E3"] == "tbl-3"
    assert mig.stats["tablas adoptadas (ya existían)"] == 2
    # sus columnas mapean a las vivas (para enganchar relaciones)
    assert mig.col_pid["A1"] == "col-1" and mig.col_pid["C1"] == "col-5"
    # el conflicto quedó en el reporte con el score de cada lado
    won = {c["key"]: c["won"] for c in mig.report["conflicts"]}
    assert won == {"S1.TAB_A": "db", "S1.TAB_C": "db"}


def test_tabla_nueva_se_crea_y_cuelga_de_la_adoptada(corrida):
    db, mig = corrida
    tabs = {d["physicalName"]: d for d in db.data["canonical_tables"].values()}
    assert "TAB_B" in tabs and tabs["TAB_B"]["_id"] == mig.pid("E2")
    # la FK nueva TAB_A→TAB_B apunta a la tabla VIVA y su columna viva
    nuevos = [r for r in db.data["relationships"].values()
              if r["_id"] not in ("rel-1", "rel-2")]
    assert len(nuevos) == 1
    r = nuevos[0]
    assert r["parentTableId"] == "tbl-1" and r["childTableId"] == mig.pid("E2")
    assert r["pairs"][0]["parentColumnId"] == "col-1"


# ── Doc 75 · proyecto primero, docs estampados, aislamiento ───────────────
def test_proyecto_resuelto_antes_de_escribir_y_docs_estampados(corrida):
    db, mig = corrida
    assert mig.project_id == "proj-1"
    assert mig.stats.get("proyectos creados", 0) == 0
    for coll in ("canonical_tables", "canonical_columns", "relationships", "views",
                 "subject_areas", "folders", "schemas", "parent_domains",
                 "glossary_terms", "udp_definitions"):
        for d in db.data[coll].values():
            assert d.get("projectId") == "proj-1", (coll, d)
    # naming_config sembrado por proyecto (un doc por scope, sin pisar)
    assert {d["_id"] for d in db.data["naming_config"].values()} == {"proj-1:table", "proj-1:column"}
    assert all(d["projectId"] == "proj-1" for d in db.data["naming_config"].values())


def test_naming_config_existente_no_se_pisa(corrida):
    db, _ = corrida
    db.data["naming_config"]["proj-1:table"]["separator"] = "_"
    Migrator(db, modelo_archivo_2(), "Familia DDV", None).run()
    assert db.data["naming_config"]["proj-1:table"]["separator"] == "_"


def test_proyecto_nuevo_se_crea_con_descripcion():
    db = FakeDb()
    mig = Migrator(db, modelo_archivo_2(), "Nuevo", None, description="Desde el manifiesto")
    mig.run()
    (proj,) = db.data["projects"].values()
    assert proj["_id"] == mig.project_id and proj["name"] == "Nuevo"
    assert proj["description"] == "Desde el manifiesto" and proj["flgactive"] is True
    assert "projectId" not in proj
    assert mig.stats["proyectos creados"] == 1
    assert mig.project_id == pol.platform_id("project|Nuevo")


def test_otro_proyecto_no_ve_las_tablas_del_primero():
    """Doc 75: mismo Long_Id y misma clave natural en DOS proyectos = dos docs."""
    db = base_db()
    mig_b = Migrator(db, modelo_archivo_2(), "Otro proyecto", None)
    mig_b.run()
    assert mig_b.project_id != "proj-1"
    assert mig_b.table_pid["E1"] == mig_b.pid("E1") != "tbl-1"          # sin adopción cross-project
    assert mig_b.stats.get("tablas adoptadas (ya existían)", 0) == 0
    assert mig_b.report["conflicts"] == []
    tabs = [d for d in db.data["canonical_tables"].values() if d["physicalName"] == "TAB_A"]
    assert {d["projectId"] for d in tabs} == {"proj-1", mig_b.project_id}   # TAB_A en A y en B
    # el proyecto A quedó intacto (ni una relación ni un canvas suyo tocado)
    assert set(db.data["relationships"]) >= {"rel-1", "rel-2"}
    assert db.data["subject_areas"]["cv-1"]["tableIds"] == ["tbl-1", "tbl-3"]
    # el mismo Long_Id da ids DISTINTOS en cada proyecto
    mig_a = Migrator(db, modelo_archivo_2(), "Familia DDV", None)
    assert mig_a.pid("E2") != mig_b.pid("E2")


def test_dominio_con_tipo_distinto_entre_archivos_va_al_reporte():
    db = FakeDb({"projects": {"proj-1": {"_id": "proj-1", "name": "Familia DDV"}},
                 "parent_domains": {"dom-1": {"_id": "dom-1", "projectId": "proj-1", "name": "Codigo",
                                              "defaultDataType": "VARCHAR(20)"}}})
    m = modelo_archivo_2()
    m.domains = {"D1": ep.ErwinDomain(id="D1", name="Codigo", builtin=False, data_type="VARCHAR(30)",
                                      parent_ref=None, definition="", physical_type="VARCHAR(30)")}
    mig = Migrator(db, m, "Familia DDV", None)
    mig.run()
    assert db.data["parent_domains"]["dom-1"]["defaultDataType"] == "VARCHAR(20)"     # el primero gana
    assert mig.domain_pid["D1"] == "dom-1"
    assert mig.report["domain_conflicts"] == [{"name": "Codigo", "kept": "VARCHAR(20)", "ignored": "VARCHAR(30)"}]
    assert mig.stats["dominios en conflicto (tipo distinto)"] == 1
    assert mig.stats["dominios reusados"] == 1


def test_dominio_igual_entre_archivos_se_reusa_sin_conflicto():
    db = FakeDb({"projects": {"proj-1": {"_id": "proj-1", "name": "Familia DDV"}},
                 "parent_domains": {"dom-1": {"_id": "dom-1", "projectId": "proj-1", "name": "Codigo",
                                              "defaultDataType": "VARCHAR(20)"}}})
    m = modelo_archivo_2()
    m.domains = {"D1": ep.ErwinDomain(id="D1", name="codigo", builtin=False, data_type="Varchar (20)",
                                      parent_ref=None, definition="", physical_type="Varchar (20)")}
    mig = Migrator(db, m, "Familia DDV", None)
    mig.run()
    assert mig.report["domain_conflicts"] == []
    assert mig.stats["dominios reusados"] == 1


# ── R4 · dedup de relaciones ──────────────────────────────────────────────
def test_fk_repetida_entre_archivos_no_se_duplica(corrida):
    db, mig = corrida
    # R3 (TAB_A→TAB_C, CODA→CODC) ya existía como rel-1 → se reusa
    assert mig.stats["relaciones reusadas (ya existían — R4)"] == 1
    assert len(db.data["relationships"]) == 3  # rel-1 + rel-2 + la nueva de TAB_B


# ── Duplicados internos → sufijo _DUPn (política 2026-08-22) ──────────────
def test_copia_interna_migra_como_tabla_propia_con_sufijo(corrida):
    db, mig = corrida
    # E4 (copia de TAB_A) ya NO es alias: migra como tabla real _DUP1, con su
    # contenido intacto (lógico igual, columnas propias).
    assert mig.table_pid["E4"] == mig.pid("E4") != mig.table_pid["E1"]
    dup = db.data["canonical_tables"][mig.pid("E4")]
    assert dup["physicalName"] == "TAB_A_DUP1"
    assert dup["logicalName"] == "tab_a"          # solo cambia el físico
    assert mig.col_pid["A1B"] == mig.pid("A1B") != "col-1"
    cols_dup = [c for c in db.data["canonical_columns"].values()
                if c.get("tableId") == mig.pid("E4")]
    assert [c["physicalName"] for c in cols_dup] == ["CODA"]
    # reporte transparente: el mapeo exacto de renombres
    rep = mig.report["renamed_dups"][0]
    assert rep["key"] == "S1.TAB_A" and rep["kept"] == "TAB_A" and rep["copies"] == 2
    assert rep["renamed"] == [{"erwinLongId": "E4", "logicalName": "tab_a",
                               "from": "TAB_A", "to": "TAB_A_DUP1"}]
    assert mig.stats["tablas duplicadas → renombradas con sufijo _DUPn"] == 1


# ── vistas sobre tablas adoptadas ─────────────────────────────────────────
def test_vista_espejo_cuelga_de_la_tabla_viva(corrida):
    db, _ = corrida
    vistas = [v for v in db.data["views"].values() if v.get("name") == "TAB_A_VU"]
    assert len(vistas) == 1
    assert vistas[0]["tableId"] == "tbl-1"
    assert vistas[0]["sources"][0]["column"] == "CODA"


# ── Doc 61 r2 · catálogo FIJO (reemplaza la poda A4) ──────────────────────
def test_def_udp_basura_no_se_crea(corrida):
    from scripts.erwin_migration.standard_udps import FIXED_UDPS
    db, mig = corrida
    # El catálogo fijo se siembra COMPLETO aunque nadie lo use… (doc 69: una
    # def por nivel + FACETA + nombre — los homónimos lógico/físico son dos).
    defs = {(d["level"], d["view"], d["name"]) for d in db.data["udp_definitions"].values()}
    assert len(defs) == len(FIXED_UDPS)
    assert ("view", "physical", "Tipo de Vista") in defs
    vt = next(d for d in db.data["udp_definitions"].values()
              if d["level"] == "view" and d["name"] == "Tipo de Vista")
    assert vt["allowedValues"] == ["Regular", "Personalizada"]
    assert vt["defaultValue"] == "Regular"
    # …y la def del XML fuera del catálogo NO se crea (al reporte).
    skipped = [d for d in mig.report["udp_defs_skipped"] if d["name"] == "Basura"]
    assert skipped and skipped[0]["motivo"] == "fuera del catálogo fijo"


# ── schemas ───────────────────────────────────────────────────────────────
def test_schema_existente_reusado_y_nuevo_creado(corrida):
    db, mig = corrida
    names = sorted(d["name"] for d in db.data["schemas"].values())
    assert names == ["S1", "S1V"]
    assert mig.stats["schemas reusados"] == 1


# ── R8 · fusión de folder y canvas homónimos ──────────────────────────────
def test_folder_y_canvas_homonimos_se_fusionan(corrida):
    db, mig = corrida
    assert len(db.data["folders"]) == 1            # AREA reusado, no duplicado
    assert mig.stats["folders reusados (homónimos de otro archivo)"] == 1
    cv = db.data["subject_areas"]["cv-1"]          # DIAG fusionado en cv-1
    assert len(db.data["subject_areas"]) == 2      # cv-1 y cv-2, sin nuevos
    # unión de tablas: las vivas + la copia _DUP (nodo propio, política
    # 2026-08-22) + TAB_B nueva — en el orden del diagrama (E1, E4, E2)
    assert cv["tableIds"] == ["tbl-1", "tbl-3", mig.pid("E4"), mig.pid("E2")]
    # el layout trabajado se preserva
    assert cv["layout"]["tbl-1"] == {"x": 11, "y": 22}


def test_canvas_siembra_membresia_de_vistas(corrida):
    """Doc 70: la vista dibujada en el diagrama de Erwin es MIEMBRO del canvas
    (`viewIds`), además de tener posición en el layout; el canvas sin vistas
    queda con lista explícita vacía (no legacy None)."""
    db, mig = corrida
    cv = db.data["subject_areas"]["cv-1"]
    v1 = mig.pid("V1")
    assert cv["viewIds"] == [v1]
    assert v1 in cv["layout"]
    assert v1 not in cv["tableIds"]


# ── política _DUPn (2026-08-22): correlativos, re-runs y colisión global ──
def _modelo_party_triple() -> ep.ErwinModel:
    """Tres copias de PARTY en el mismo schema; E2 es la MÁS usada (padre de
    una relación) → conserva el nombre limpio; E1 y E3 → _DUP1/_DUP2 en orden
    de archivo."""
    m = ep.ErwinModel(name="M")
    m.entities = {
        "E1": entity("E1", "PARTY", [attr("A1", "E1", "COD", 1)]),
        "E2": entity("E2", "PARTY", [attr("B1", "E2", "COD", 1)], pk=("B1",)),
        "E3": entity("E3", "PARTY", [attr("C1", "E3", "COD", 1)]),
        "E4": entity("E4", "OTRA", [
            attr("D1", "E4", "COD", 1, parent_attr="B1", parent_rel="R1")]),
    }
    m.relationships = {"R1": rel("R1", "E2", "E4")}
    m.hive_dbs = {"S1": ["E1", "E2", "E3", "E4"]}
    return m


def test_sufijos_correlativos_y_rerun_estable():
    db = FakeDb()
    mig = Migrator(db, _modelo_party_triple(), "Fam", None)
    mig.run()

    by_erwin = {d["erwinLongId"]: d["physicalName"]
                for d in db.data["canonical_tables"].values()}
    assert by_erwin == {"E1": "PARTY_DUP1", "E2": "PARTY",
                        "E3": "PARTY_DUP2", "E4": "OTRA"}
    assert mig.stats["tablas duplicadas → renombradas con sufijo _DUPn"] == 2
    n0 = len(db.data["canonical_tables"])

    # re-run del MISMO archivo: continuidad por erwinLongId — mismos nombres,
    # cero docs nuevos y cero ratchet (_DUP3 jamás aparece)
    mig2 = Migrator(db, _modelo_party_triple(), "Fam", None)
    mig2.run()
    by_erwin2 = {d["erwinLongId"]: d["physicalName"]
                 for d in db.data["canonical_tables"].values()}
    assert by_erwin2 == by_erwin
    assert len(db.data["canonical_tables"]) == n0
    assert mig2.report["renamed_dups"] == []
    assert mig2.stats["tablas duplicadas → renombradas con sufijo _DUPn"] == 0


def test_colision_entre_esquemas_del_mismo_proyecto_tambien_sufija():
    """Doc 50 + doc 75: el físico es único POR PROYECTO — un homónimo en OTRO
    esquema del mismo proyecto también fuerza el sufijo."""
    db = FakeDb({"projects": {"proj-f": {"_id": "proj-f", "name": "Fam"}},
                 "canonical_tables": {
        "tbl-x": {"_id": "tbl-x", "schema": "OTRO", "physicalName": "PARTY", "projectId": "proj-f"}}})
    m = ep.ErwinModel(name="M")
    m.entities = {"E1": entity("E1", "PARTY", [attr("A1", "E1", "COD", 1)])}
    m.hive_dbs = {"S1": ["E1"]}
    mig = Migrator(db, m, "Fam", None)
    mig.run()

    assert mig.project_id == "proj-f"
    mine = db.data["canonical_tables"][mig.pid("E1")]
    assert mine["physicalName"] == "PARTY_DUP1" and mine["schema"] == "S1"
    assert db.data["canonical_tables"]["tbl-x"]["physicalName"] == "PARTY"  # intacta
    rep = mig.report["renamed_dups"][0]
    assert rep["renamed"][0]["to"] == "PARTY_DUP1"

    # re-run: el erwinLongId ancla el nombre — sin _DUP2
    mig2 = Migrator(db, m, "Fam", None)
    mig2.run()
    assert db.data["canonical_tables"][mig.pid("E1")]["physicalName"] == "PARTY_DUP1"
    assert len(db.data["canonical_tables"]) == 2


def test_homonimo_en_otro_proyecto_no_sufija():
    """Doc 75: la unicidad es por proyecto — PARTY en otro proyecto no estorba."""
    db = FakeDb({"projects": {"proj-z": {"_id": "proj-z", "name": "Otro"}},
                 "canonical_tables": {
        "tbl-x": {"_id": "tbl-x", "schema": "S1", "physicalName": "PARTY", "projectId": "proj-z"}}})
    m = ep.ErwinModel(name="M")
    m.entities = {"E1": entity("E1", "PARTY", [attr("A1", "E1", "COD", 1)])}
    m.hive_dbs = {"S1": ["E1"]}
    mig = Migrator(db, m, "Fam", None)
    mig.run()
    assert mig.project_id != "proj-z"
    assert db.data["canonical_tables"][mig.pid("E1")]["physicalName"] == "PARTY"
    assert mig.report["renamed_dups"] == []


def test_glosario_conflicto_vs_bd_se_reporta_y_la_bd_gana():
    db = FakeDb({"projects": {"proj-f": {"_id": "proj-f", "name": "Fam"}},
                 "glossary_terms": {
        "g1": {"_id": "g1", "term": "Monto", "abbrev": "MTO", "projectId": "proj-f"}}})
    m = ep.ErwinModel(name="M")
    m.glossary = [("Monto", "MTOS"), ("Codigo", "COD"), ("Codigo", "CODIG")]
    mig = Migrator(db, m, "Fam", None)
    mig.run()

    # la BD nunca se pisa; el término nuevo se SUMA (full outer join)
    assert db.data["glossary_terms"]["g1"]["abbrev"] == "MTO"
    created = [g for g in db.data["glossary_terms"].values() if g.get("term") == "Codigo"]
    assert len(created) == 1 and created[0]["abbrev"] == "COD"
    # ambas incongruencias quedan reportadas (vs BD y duplicado interno)
    conflicts = {(c["term"], c["kept"], c["ignored"])
                 for c in mig.report["glossary_conflicts"]}
    assert conflicts == {("Monto", "MTO", "MTOS"), ("Codigo", "COD", "CODIG")}
    assert mig.stats["glosario en conflicto (abbrev distinta)"] == 2


# ── doc 53 · subcategorías (supertipo→subtipo) ────────────────────────────
def _modelo_subtipos() -> ep.ErwinModel:
    """Party con dos subtipos (Individuo, Organizacion) agrupados por UN
    símbolo; la PK del supertipo viene heredada en cada hijo (como Erwin)."""
    m = ep.ErwinModel(name="M Subtipos")
    e1 = entity("E1", "PARTY", [attr("A1", "E1", "CODPARTY", 1)], pk=("A1",))
    e2 = entity("E2", "INDIVIDUO", [
        attr("B1", "E2", "CODPARTY", 1, parent_attr="A1", parent_rel="R9")],
        pk=("B1",))
    e3 = entity("E3", "ORGANIZACION", [
        attr("C1", "E3", "CODPARTY", 1, parent_attr="A1", parent_rel="R9B")],
        pk=("C1",))
    m.entities = {e.id: e for e in (e1, e2, e3)}
    m.relationships = {
        "R9": rel("R9", "E1", "E2", rel_type=ep.REL_SUBTYPE),
        "R9B": rel("R9B", "E1", "E3", rel_type=ep.REL_SUBTYPE),
    }
    m.subtype_symbols = {"SY1": ep.ErwinSubtypeSymbol(
        id="SY1", name="Subtype_Symbol_Party", rel_refs=["R9", "R9B"])}
    m.hive_dbs = {"S1": ["E1", "E2", "E3"]}
    return m


def test_subtipo_migra_como_subcategoria_1a1_con_simbolo_compartido():
    db = FakeDb()
    mig = Migrator(db, _modelo_subtipos(), "Fam", None)
    mig.run()

    rels = {r["erwinLongId"]: r for r in db.data["relationships"].values()}
    assert set(rels) == {"R9", "R9B"}
    r9 = rels["R9"]
    assert r9["subcategory"] is True
    # el símbolo agrupa: ambas aristas comparten el MISMO subtypeSymbolId
    assert r9["subtypeSymbolId"] == mig.pid("SY1")
    assert rels["R9B"]["subtypeSymbolId"] == mig.pid("SY1")
    # ES-UN: identifying + 1:1 estricto (ignora cardinality/null_option crudos)
    assert r9["identifying"] is True
    assert (r9["parentCardinality"], r9["childCardinality"]) == ("one", "one")
    assert r9["pairs"] == [{"parentColumnId": mig.pid("A1"),
                            "childColumnId": mig.pid("B1"),
                            "roleName": None}]
    assert mig.stats["relaciones de subcategoría (supertipo→subtipo)"] == 2
    assert mig.stats["relaciones"] == 2
    assert mig.warnings == []


def test_subtipo_sin_simbolo_usa_sintetico_y_avisa():
    db = FakeDb()
    m = _modelo_subtipos()
    m.subtype_symbols = {}          # nadie agrupa las Type 9
    mig = Migrator(db, m, "Fam", None)
    mig.run()

    rels = {r["erwinLongId"]: r for r in db.data["relationships"].values()}
    assert rels["R9"]["subcategory"] is True
    assert rels["R9"]["subtypeSymbolId"] == mig.pid("subsym|R9")
    assert rels["R9B"]["subtypeSymbolId"] == mig.pid("subsym|R9B")
    assert sum("símbolo sintético" in w for w in mig.warnings) == 2


def test_subcategoria_no_se_fusiona_con_identifying_de_iguales_extremos():
    """R4 multi-archivo: una identifying con los MISMOS extremos y pares por
    nombre que una subcategoría ya migrada NO debe reusarla (el marcador de la
    clave natural las separa); el mismo subtipo re-llegado SÍ se reusa."""
    db = FakeDb()
    Migrator(db, _modelo_subtipos(), "Fam", None).run()
    n0 = len(db.data["relationships"])
    assert n0 == 2                      # R9 + R9B

    # archivo 2: misma pareja PARTY→INDIVIDUO y mismo par CODPARTY→CODPARTY,
    # pero como identifying "normal" → doc NUEVO, sin dedup
    m2 = ep.ErwinModel(name="M2")
    m2.entities = {
        "E1": entity("E1", "PARTY", [attr("A1", "E1", "CODPARTY", 1)], pk=("A1",)),
        "E2": entity("E2", "INDIVIDUO", [
            attr("B1", "E2", "CODPARTY", 1, parent_attr="A1", parent_rel="RID")],
            pk=("B1",)),
    }
    m2.relationships = {"RID": ep.ErwinRelationship(
        id="RID", name="RID", rel_type=ep.REL_IDENTIFYING, cardinality="-1",
        parent_ref="E1", child_ref="E2", null_option="101")}
    m2.hive_dbs = {"S1": ["E1", "E2"]}
    mig2 = Migrator(db, m2, "Fam", None)
    mig2.run()
    assert mig2.stats["relaciones reusadas (ya existían — R4)"] == 0
    assert len(db.data["relationships"]) == n0 + 1

    # archivo 3: el MISMO modelo de subtipos otra vez → ambas se reusan
    mig3 = Migrator(db, _modelo_subtipos(), "Fam", None)
    mig3.run()
    assert mig3.stats["relaciones reusadas (ya existían — R4)"] == 2
    assert len(db.data["relationships"]) == n0 + 1


# ── R2 · cuando el archivo está MÁS usado, gana y actualiza en su sitio ───
def test_gana_el_archivo_actualiza_en_su_sitio_y_reusa_ids_de_columna():
    P = {"projectId": "proj-f"}
    db = FakeDb({
        "projects": {"proj-f": {"_id": "proj-f", "name": "Fam"}},
        "canonical_tables": {
            "tbl-1": {"_id": "tbl-1", "schema": "S1", "physicalName": "TAB_A", **P}},
        "canonical_columns": {
            "col-1": {"_id": "col-1", "tableId": "tbl-1", "physicalName": "CODA",
                      "erwinLongId": "OLD-A1", **P},
            "col-2": {"_id": "col-2", "tableId": "tbl-1", "physicalName": "OBSOLETA",
                      "erwinLongId": "OLD-A2", **P}},
        "views": {
            "vw-1": {"_id": "vw-1", "schema": "S1V", "name": "TAB_A_VU",
                     "sourceTableIds": ["tbl-1"], **P}},
    })  # la tabla viva NO se usa en nada → score BD = 1 (solo su vista espejo)
    m = ep.ErwinModel(name="M")
    e1 = entity("E1", "TAB_A", [attr("A1", "E1", "CODA", 1),
                                attr("A2", "E1", "NUEVA", 2)], pk=("A1",))
    e2 = entity("E2", "TAB_B", [
        attr("B1", "E2", "CODA", 1, parent_attr="A1", parent_rel="R1")])
    m.entities = {"E1": e1, "E2": e2}
    m.relationships = {"R1": rel("R1", "E1", "E2"),
                       "RV1": rel("RV1", "E1", "V1", rel_type=ep.REL_TABLE_TO_VIEW)}
    m.views = {"V1": ep.ErwinView(
        id="V1", name="TAB_A_VU", definition="", comment="",
        attributes=[attr("VA1", "V1", "CODA", 1, kind="View", parent_attr="A1")])}
    m.hive_dbs = {"S1": ["E1", "E2"], "S1V": ["V1"]}
    mig = Migrator(db, m, "Fam", None)
    mig.run()

    assert mig.table_verdicts["tbl-1"] == "xml"
    t = db.data["canonical_tables"]["tbl-1"]
    assert t["erwinLongId"] == "E1" and t["migratedFrom"] == "erwin"
    # CODA conserva su _id vivo (las relaciones no se rompen)…
    assert db.data["canonical_columns"]["col-1"]["erwinLongId"] == "A1"
    # …la columna nueva se crea y la que este archivo NO trae se CONSERVA
    # (doc 73 D10: venía de OTRO archivo de la familia — el export lógico de
    # UDV no trae las columnas físicas; retirarlas dejaba relaciones huérfanas)
    assert mig.pid("A2") in db.data["canonical_columns"]
    assert db.data["canonical_columns"]["col-2"].get("flgactive", True) is not False
    assert mig.stats["columnas de otro archivo conservadas (tabla actualizada por uso)"] == 1
    # la vista espejo sigue la suerte de su tabla: actualizada EN vw-1
    assert db.data["views"]["vw-1"]["erwinLongId"] == "V1"
    assert mig.stats["tablas actualizadas (ganó el archivo por uso)"] == 1


def test_rerun_del_mismo_archivo_si_retira_las_columnas_ausentes():
    """Doc 73 D10: el saneo de columnas sigue vivo para el RE-RUN del mismo
    archivo (misma entidad Erwin ⇒ mismo pid): lo que ya no viene, se retira."""
    pid = pol.project_scoped_id("proj-f", "E1")
    P = {"projectId": "proj-f"}
    db = FakeDb({
        "projects": {"proj-f": {"_id": "proj-f", "name": "Fam"}},
        "canonical_tables": {pid: {"_id": pid, "schema": "S1", "physicalName": "TAB_A", "erwinLongId": "E1", **P}},
        "canonical_columns": {
            "c-a1": {"_id": "c-a1", "tableId": pid, "physicalName": "CODA", "erwinLongId": "A1", **P},
            "c-a9": {"_id": "c-a9", "tableId": pid, "physicalName": "OBSOLETA", "erwinLongId": "A9", **P}},
    })
    m = ep.ErwinModel(name="M")
    m.entities = {"E1": entity("E1", "TAB_A", [attr("A1", "E1", "CODA", 1)], pk=("A1",))}
    m.hive_dbs = {"S1": ["E1"]}
    mig = Migrator(db, m, "Fam", None)
    mig.run()
    assert db.data["canonical_columns"]["c-a1"].get("flgactive", True) is not False
    assert db.data["canonical_columns"]["c-a9"]["flgactive"] is False
    assert mig.stats["columnas migradas retiradas (ya no están en el XML)"] == 1
    assert "columnas de otro archivo conservadas (tabla actualizada por uso)" not in mig.stats


# ── Doc 54 §9 · capa de carpeta por archivo de origen ─────────────────────
def test_capa_de_origen_agrupa_y_separa_homonimos_entre_archivos():
    db = base_db()   # BD legacy PLANA: fold-1 "AREA" en la raíz
    mig = Migrator(db, modelo_archivo_2(), "Familia DDV", None,
                   source_folder="Otros")
    mig.run()
    folders = db.data["folders"]
    src = [f for f in folders.values() if f["name"] == "Otros"]
    assert len(src) == 1 and src[0].get("parentFolderId") is None
    # la SA "AREA" del archivo ya NO se fusiona con el fold-1 legacy de la
    # raíz: crea su propia carpeta bajo "Otros"
    areas = [f for f in folders.values() if f["name"] == "AREA"]
    assert {f.get("parentFolderId") for f in areas} == {None, src[0]["_id"]}
    nueva = next(f for f in areas if f.get("parentFolderId") == src[0]["_id"])
    canv = next(c for c in db.data["subject_areas"].values()
                if c["name"] == "DIAG" and c["_id"] != "cv-1")
    assert canv["folderId"] == nueva["_id"]
    assert db.data["subject_areas"]["cv-1"]["folderId"] == "fold-1"


def test_capa_de_origen_idempotente_en_rerun():
    db = base_db()
    for _ in range(2):
        Migrator(db, modelo_archivo_2(), "Familia DDV", None,
                 source_folder="Otros").run()
    fl = db.data["folders"].values()
    assert len([f for f in fl if f["name"] == "Otros"]) == 1
    assert len([f for f in fl if f["name"] == "AREA"]) == 2


def test_parse_mart_locator_capas():
    got = pol.parse_mart_locator(
        "erwin://Mart://Mart/Modelo UDV/Modelo Logico/UDV Logico V0.618?&version=1")
    assert got == {"project": "Modelo UDV", "domain": "Modelo Logico",
                   "model": "UDV Logico V0.618"}
    assert pol.parse_mart_locator("Mart://Mart/P/M")["domain"] == ""
    assert pol.parse_mart_locator("erwin://file://C:/x.erwin") is None


# ── Doc 62 · homologación de defaults de dominio al sembrar ───────────────
def test_dominios_se_siembran_con_tipo_homologado():
    """El XML trae la grafía cruda de Erwin (`Array`, `BIG INTEGER`,
    `DECIMAL (22,4)`); la siembra debe escribir la CANÓNICA de la plataforma."""
    m = modelo_archivo_2()
    m.domains = {
        "D1": ep.ErwinDomain(id="D1", name="ColArray", builtin=False,
                             data_type="Array", parent_ref=None, definition=""),
        "D2": ep.ErwinDomain(id="D2", name="BigInt", builtin=False,
                             data_type="BIG INTEGER", parent_ref=None, definition=""),
        "D3": ep.ErwinDomain(id="D3", name="Monto", builtin=False,
                             data_type="DECIMAL (22,4)", parent_ref=None, definition=""),
        "D4": ep.ErwinDomain(id="D4", name="SinTipo", builtin=False,
                             data_type="", parent_ref=None, definition=""),
    }
    db = base_db()
    mig = Migrator(db, m, "Familia DDV", None)
    mig.run()
    tipos = {d["name"]: d["defaultDataType"]
             for d in db.data["parent_domains"].values()}
    assert tipos == {"ColArray": "ARRAY<>", "BigInt": "BIGINT",
                     "Monto": "DECIMAL(22,4)", "SinTipo": "STRING"}
