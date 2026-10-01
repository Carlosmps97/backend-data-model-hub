"""Doc 105 — kit Erwin: una tabla BORRADA en la app que el XML todavía trae.

El kit no la revive (`flgactive` sólo al insertar; revivir lo decide el owner),
pero la re-escribía muerta y le colgaba lo NUEVO del XML ACTIVO: columnas
nuevas bajo una tabla muerta, relaciones nuevas con un extremo muerto y vistas
nuevas cuya única fuente es esa tabla (huérfanas: la auditoría C4/C6 las
marcaba). Ahora la tabla se omite entera en la corrida —ni se revive ni se
toca— con todo lo que dependa de ella, y la estadística y el reporte lo dicen."""
from __future__ import annotations

import copy

from scripts.erwin_migration import erwin_parser as ep
from scripts.erwin_migration.migrate import Migrator
from tests.erwin_migration.test_migrate_merge import attr, entity, rel
from tests.erwin_migration.test_subcategory_projection_doc100 import ProjectingDb

SKIPPED_TABLES = "tablas borradas en la app (no se reviven; se omiten con sus columnas, relaciones y vistas)"


def _modelo(con_nuevas: bool) -> ep.ErwinModel:
    """TAB_UNO (COD) y TAB_NUEVE (ID9). La versión nueva del XML le suma a
    TAB_UNO la columna NUEVA, la FK TAB_UNO→TAB_NUEVE (columna COD en
    TAB_NUEVE) y la vista espejo TAB_UNO_VU."""
    m = ep.ErwinModel(name="Archivo")
    e1 = entity("E1", "TAB_UNO", [attr("A1", "E1", "COD", 1)]
                + ([attr("A2", "E1", "NUEVA", 2)] if con_nuevas else []), pk=("A1",))
    e9 = entity("E9", "TAB_NUEVE", [attr("B1", "E9", "ID9", 1)]
                + ([attr("B2", "E9", "COD", 2, parent_attr="A1", parent_rel="R19")] if con_nuevas else []),
                pk=("B1",))
    m.entities = {"E1": e1, "E9": e9}
    m.hive_dbs = {"S1": ["E1", "E9"]}
    if con_nuevas:
        m.relationships = {
            "R19": rel("R19", "E1", "E9"),
            "RV1": ep.ErwinRelationship(id="RV1", name="rv", rel_type=ep.REL_TABLE_TO_VIEW, cardinality="-3",
                                        parent_ref="E1", child_ref="V1", null_option="100")}
        m.views = {"V1": ep.ErwinView(id="V1", name="TAB_UNO_VU", definition="", comment="",
                                      attributes=[attr("VA1", "V1", "COD", 1, kind="View",
                                                       parent_attr="A1", parent_rel="RV1")])}
        m.hive_dbs["S1V"] = ["V1"]
    return m


def _borrar_tabla_en_la_app(db, table_id: str) -> None:
    """Lo que deja el publish que borra la tabla: ella y sus columnas inactivas."""
    db.data["canonical_tables"][table_id]["flgactive"] = False
    for c in db.data["canonical_columns"].values():
        if c["tableId"] == table_id:
            c["flgactive"] = False


def _vivo(doc: dict) -> bool:
    return doc.get("flgactive") is not False


def test_tabla_borrada_en_la_app_no_revive_ni_cuelga_columnas_relaciones_ni_vistas_nuevas():
    db = ProjectingDb()
    primera = Migrator(db, _modelo(False), "Fam", None)
    primera.run()
    t1, t9 = primera.table_pid["E1"], primera.table_pid["E9"]
    _borrar_tabla_en_la_app(db, t1)
    antes = copy.deepcopy(db.data["canonical_tables"][t1])

    mig = Migrator(db, _modelo(True), "Fam", None)
    mig.run()

    assert db.data["canonical_tables"][t1] == antes                          # ni revive ni se toca
    assert [c["physicalName"] for c in db.data["canonical_columns"].values()
            if c["tableId"] == t1 and _vivo(c)] == []                        # sin columnas vivas colgando
    assert mig.pid("A2") not in db.data["canonical_columns"]                 # la NUEVA no se inserta
    assert [r for r in db.data.get("relationships", {}).values()
            if t1 in (r["parentTableId"], r["childTableId"])] == []          # ni la relación nueva
    assert mig.pid("V1") not in db.data.get("views", {})                     # ni la vista espejo nueva
    assert _vivo(db.data["canonical_columns"][mig.pid("B2")])                # TAB_NUEVE (viva) sí suma su columna
    assert db.data["canonical_columns"][mig.pid("B2")]["tableId"] == t9
    assert mig.stats[SKIPPED_TABLES] == 1
    assert mig.stats["relaciones omitidas (tabla borrada en la app)"] == 1
    assert mig.stats["vistas omitidas (fuente borrada en la app)"] == 1
    assert mig.report["deleted_in_app"] == [{"key": "S1.TAB_UNO", "tableId": t1, "columns": 2}]


def test_control_la_misma_recorrida_sin_borrar_escribe_todo():
    db = ProjectingDb()
    Migrator(db, _modelo(False), "Fam", None).run()
    mig = Migrator(db, _modelo(True), "Fam", None)
    mig.run()
    assert _vivo(db.data["canonical_columns"][mig.pid("A2")])
    assert len(db.data["relationships"]) == 1 and _vivo(db.data["views"][mig.pid("V1")])
    assert SKIPPED_TABLES not in mig.stats and mig.report["deleted_in_app"] == []


# ── Ronda 4 (R8): la FAMILIA y las vistas con una fuente borrada ───────────
# `existing_id`/`taken` salen sólo de lo VIVO: con TAB_A borrada, el archivo que
# la había adoptado caía a SU `own_pid` (distinto del borrado) y la daba de alta
# NUEVA y viva al re-correr la carpeta. Y una vista multi-fuente con una fuente
# borrada re-apuntaba la columna de esa fuente a OTRA tabla (`source_pids[0]`):
# una columna inexistente para el DDL y un linaje falso.

from tests.erwin_migration.test_canvas_merge_doc105 import _archivo, _canvas  # noqa: E402

SKIPPED_VIEWS = "vistas borradas en la app (no se reviven)"


def _vivas(db, coll: str, **match) -> list[dict]:
    return [d for d in db.data.get(coll, {}).values()
            if _vivo(d) and all(d.get(k) == v for k, v in match.items())]


def test_r8_recorrida_de_la_familia_no_revive_la_tabla_borrada_con_otro_id():
    db = ProjectingDb()
    m1 = Migrator(db, _archivo("1", "TAB_A"), "Familia", None)
    m1.run()
    m2 = Migrator(db, _archivo("2", "TAB_A"), "Familia", None)             # misma tabla: la adopta
    m2.run()
    t = m1.table_pid["E1"]
    assert m2.table_pid["E2"] == t and len(_vivas(db, "canonical_tables", physicalName="TAB_A")) == 1
    _borrar_tabla_en_la_app(db, t)
    for v in db.data["views"].values():
        if t in (v.get("sourceTableIds") or []):
            v["flgactive"] = False
    Migrator(db, _archivo("1", "TAB_A"), "Familia", None).run()             # re-corrida de la carpeta
    otro = Migrator(db, _archivo("2", "TAB_A"), "Familia", None)
    otro.run()
    assert _vivas(db, "canonical_tables", physicalName="TAB_A") == []
    assert _vivas(db, "views", name="TAB_A_VU") == []
    assert otro.pid("E2") not in db.data["canonical_tables"]
    v = m1.view_pid["V1"]                                                    # su _vu, también borrada
    assert otro.report["deleted_in_app"] == [{"key": "S1.TAB_A", "tableId": t, "columns": 1},
                                             {"key": "S1V.TAB_A_VU", "viewId": v}]
    assert _canvas(db)["tableIds"] == [] and _canvas(db)["viewIds"] == []


def test_r8_recorrida_de_la_familia_no_revive_la_vista_borrada_con_otro_id():
    db = ProjectingDb()
    m1 = Migrator(db, _archivo("1", "TAB_A"), "Familia", None)
    m1.run()
    Migrator(db, _archivo("2", "TAB_A"), "Familia", None).run()
    v = m1.view_pid["V1"]
    db.data["views"][v]["flgactive"] = False                                 # sólo la vista, en la app
    for cv in db.data["subject_areas"].values():
        cv["viewIds"] = [x for x in cv.get("viewIds") or [] if x != v]
    Migrator(db, _archivo("1", "TAB_A"), "Familia", None).run()
    otro = Migrator(db, _archivo("2", "TAB_A"), "Familia", None)
    otro.run()
    assert _vivas(db, "views", name="TAB_A_VU") == [] and otro.pid("V2") not in db.data["views"]
    assert otro.stats[SKIPPED_VIEWS] == 1
    assert {"key": "S1V.TAB_A_VU", "viewId": v} in otro.report["deleted_in_app"]
    assert _canvas(db)["viewIds"] == []


def _modelo_multifuente() -> ep.ErwinModel:
    """VW_JOIN = TAB_UNO.COD + TAB_DOS.ID2, dibujada con sus fuentes."""
    m = ep.ErwinModel(name="Archivo")
    m.entities = {"E1": entity("E1", "TAB_UNO", [attr("A1", "E1", "COD", 1)], pk=("A1",)),
                  "E2": entity("E2", "TAB_DOS", [attr("B1", "E2", "ID2", 1)], pk=("B1",))}
    m.relationships = {
        rid: ep.ErwinRelationship(id=rid, name=rid, rel_type=ep.REL_TABLE_TO_VIEW, cardinality="-3",
                                  parent_ref=parent, child_ref="V1", null_option="100")
        for rid, parent in (("RV1", "E1"), ("RV2", "E2"))}
    m.views = {"V1": ep.ErwinView(id="V1", name="VW_JOIN", definition="", comment="", attributes=[
        attr("VA1", "V1", "COD", 1, kind="View", parent_attr="A1", parent_rel="RV1"),
        attr("VA2", "V1", "ID2", 2, kind="View", parent_attr="B1", parent_rel="RV2")])}
    m.hive_dbs = {"S1": ["E1", "E2"], "S1V": ["V1"]}
    m.subject_areas = [{"id": "SA1", "name": "AREA", "definition": "", "order": 0}]
    m.diagrams = [ep.ErwinDiagram(id="DG1", name="DIAG", subject_area="AREA", owner_path="M.AREA",
                                  shapes=[("E1", None), ("E2", None), ("V1", None)])]
    return m


def test_r8_vista_multifuente_con_una_fuente_borrada_no_se_reapunta_a_otra_tabla():
    """El modelador borró TAB_DOS y dejó la vista sólo con TAB_UNO (lo exige el
    publish). La re-corrida NO re-escribe la vista (con la columna de la fuente
    borrada re-apuntada a TAB_UNO): queda como la dejó la app, y en el canvas."""
    db = ProjectingDb()
    first = Migrator(db, _modelo_multifuente(), "Fam", None)
    first.run()
    t1, t2, v1 = first.table_pid["E1"], first.table_pid["E2"], first.view_pid["V1"]
    _borrar_tabla_en_la_app(db, t2)
    view = db.data["views"][v1]
    view["sourceTableIds"] = [t1]
    view["sources"] = [s for s in view["sources"] if s["tableId"] == t1]
    antes = copy.deepcopy(view)
    mig = Migrator(db, _modelo_multifuente(), "Fam", None)
    mig.run()
    assert db.data["views"][v1] == antes                                     # intacta
    assert mig.stats["vistas omitidas (fuente borrada en la app)"] == 1
    assert {"view": "VW_JOIN", "reason": "source deleted in the app"} in mig.report["views_discarded"]
    cv = _canvas(db)
    assert cv["tableIds"] == [t1] and cv["viewIds"] == [v1]


def test_r8_tabla_y_vista_renombradas_en_la_app_y_borradas_se_reconocen_por_su_id():
    """Renombradas en la app ANTES de borrarlas: su clave ya no es la del XML;
    las reconoce su id (el propio del archivo) — ni se reviven ni se tocan."""
    db = ProjectingDb()
    m1 = Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None)
    m1.run()
    t, v = m1.table_pid["E1"], m1.view_pid["V1"]
    db.data["canonical_tables"][t].update(physicalName="TAB_UNO_RENOMBRADA", flgactive=False)
    db.data["views"][v].update(name="TAB_UNO_RENOMBRADA_VU", flgactive=False)
    for c in db.data["canonical_columns"].values():
        if c["tableId"] == t:
            c["flgactive"] = False
    antes = copy.deepcopy((db.data["canonical_tables"][t], db.data["views"][v]))
    mig = Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None)
    mig.run()
    assert (db.data["canonical_tables"][t], db.data["views"][v]) == antes
    assert _vivas(db, "canonical_tables") == [] and _vivas(db, "views") == []
    assert [e.get("tableId") or e.get("viewId") for e in mig.report["deleted_in_app"]] == [t, v]


# ── Ronda 5 (R13): la borrada vuelve como `_DUPn` si hay una homónima viva ──
# El físico es único POR PROYECTO: con TAB_A viva en OTRO esquema, la entidad
# del XML se renombra TAB_A_DUP1 (`_resolve_dup_names`) y la búsqueda por clave
# con ese nombre no encontraba la S1.TAB_A borrada: el otro archivo de la
# familia (que la había adoptado) la daba de alta viva como S1.TAB_A_DUP1.

def test_r13_la_borrada_no_vuelve_como_dup_con_una_homonima_viva_en_otro_esquema():
    db = ProjectingDb()
    m1 = Migrator(db, _archivo("1", "TAB_A"), "Familia", None)
    m1.run()
    Migrator(db, _archivo("2", "TAB_A"), "Familia", None).run()             # la adopta
    t = m1.table_pid["E1"]
    _borrar_tabla_en_la_app(db, t)
    for v in db.data["views"].values():
        if t in (v.get("sourceTableIds") or []):
            v["flgactive"] = False
    db.data["canonical_tables"]["t-s2"] = {"_id": "t-s2", "projectId": m1.project_id, "schema": "S2",
                                           "physicalName": "TAB_A", "logicalName": "TAB_A"}   # viva, otro esquema
    Migrator(db, _archivo("1", "TAB_A"), "Familia", None).run()
    otro = Migrator(db, _archivo("2", "TAB_A"), "Familia", None)
    otro.run()
    assert [d["_id"] for d in _vivas(db, "canonical_tables", schema="S1")] == []
    # Ronda 6 (R16/K1): la clave es el físico con el que la tabla existió (no el `_DUPn`).
    assert otro.report["deleted_in_app"][0] == {"key": "S1.TAB_A", "tableId": t, "columns": 1}
    assert _vivas(db, "canonical_tables", schema="S2") == [db.data["canonical_tables"]["t-s2"]]



# ── Ronda 6 (R16/K1): la omitida no figura como copia `_DUPn` ─────────────
# `_resolve_dup_names` la había anotado en `renamed_dups` («TAB_A →
# TAB_A_DUP1») y sumado a la estadística ANTES de omitirla: la hoja «Tablas
# homónimas» del reporte listaba una copia que no existe.

def test_r16_la_borrada_omitida_no_figura_como_copia_renombrada():
    db = ProjectingDb()
    m1 = Migrator(db, _archivo("1", "TAB_A"), "Familia", None)
    m1.run()
    Migrator(db, _archivo("2", "TAB_A"), "Familia", None).run()
    t = m1.table_pid["E1"]
    _borrar_tabla_en_la_app(db, t)
    for v in db.data["views"].values():
        if t in (v.get("sourceTableIds") or []):
            v["flgactive"] = False
    db.data["canonical_tables"]["t-s2"] = {"_id": "t-s2", "projectId": m1.project_id, "schema": "S2",
                                           "physicalName": "TAB_A", "logicalName": "TAB_A"}
    for sfx in ("1", "2"):
        run = Migrator(db, _archivo(sfx, "TAB_A"), "Familia", None)
        run.run()
        assert run.report["renamed_dups"] == [], (sfx, run.report["renamed_dups"])
        assert "tablas duplicadas → renombradas con sufijo _DUPn" not in run.stats, sfx
        assert [d["key"] for d in run.report["deleted_in_app"] if "tableId" in d] == ["S1.TAB_A"], sfx


def test_r16_control_una_copia_viva_si_figura_como_dup():
    """Control: la homónima VIVA en otro esquema sí obliga a renombrar una
    entidad que se crea — esa sí es una copia real."""
    db = ProjectingDb()
    first = Migrator(db, _archivo("1", "TAB_A"), "Familia", None)
    db.data.setdefault("canonical_tables", {})["t-s2"] = {"_id": "t-s2", "projectId": first.project_id,
                                                          "schema": "S2", "physicalName": "TAB_A",
                                                          "logicalName": "TAB_A"}
    run = Migrator(db, _archivo("1", "TAB_A"), "Familia", None)
    run.run()
    assert [r["to"] for g in run.report["renamed_dups"] for r in g["renamed"]] == ["TAB_A_DUP1"]
    assert run.stats["tablas duplicadas → renombradas con sufijo _DUPn"] == 1


# ── Ronda 7 (R18b): el fallback por clave no puede comerse una copia NUEVA ──
# El fallback por físico crudo (ronda 5) no sabía de QUÉ entidad era la tabla
# borrada: una homónima NUEVA del mismo XML (que se migra como `_DUPn`) se
# omitía como «borrada en la app» — aun con la homónima viva. Y la clave del
# reporte usaba el físico del XML: de una copia `_DUPn` borrada decía que se
# borró la ORIGINAL (que sigue viva).

def _dos_homonimas() -> ep.ErwinModel:
    """E1 y E2, homónimas S1.TAB_A en el MISMO archivo: E2 se migra TAB_A_DUP1."""
    m = ep.ErwinModel(name="Archivo")
    m.entities = {"E1": entity("E1", "TAB_A", [attr("A1", "E1", "COD", 1)], pk=("A1",)),
                  "E2": entity("E2", "TAB_A", [attr("B1", "E2", "ID", 1)], pk=("B1",))}
    m.hive_dbs = {"S1": ["E1", "E2"]}
    return m


def _solo_e1() -> ep.ErwinModel:
    m = ep.ErwinModel(name="Archivo")
    m.entities = {"E1": entity("E1", "TAB_A", [attr("A1", "E1", "COD", 1)], pk=("A1",))}
    m.hive_dbs = {"S1": ["E1"]}
    return m


def _tablas_vivas(db) -> list[str]:
    return sorted(d["physicalName"] for d in db.data["canonical_tables"].values() if _vivo(d))


def test_r18b_una_homonima_nueva_del_mismo_archivo_no_se_omite_por_la_borrada():
    db = ProjectingDb()
    first = Migrator(db, _solo_e1(), "Fam", None)
    first.run()
    t1 = first.table_pid["E1"]
    _borrar_tabla_en_la_app(db, t1)
    again = Migrator(db, _dos_homonimas(), "Fam", None)                     # E1 + E2 (nueva)
    again.run()
    assert _vivo(db.data["canonical_tables"][again.pid("E2")])               # E2 se migra
    assert [d["tableId"] for d in again.report["deleted_in_app"] if "tableId" in d] == [t1]
    assert _tablas_vivas(db) == ["TAB_A_DUP1"]


def test_r18b_con_la_homonima_viva_una_copia_nueva_tampoco_se_omite():
    db = ProjectingDb()
    first = Migrator(db, _solo_e1(), "Fam", None)
    first.run()
    _borrar_tabla_en_la_app(db, first.table_pid["E1"])
    db.data["canonical_tables"]["t-nueva"] = {"_id": "t-nueva", "projectId": first.project_id, "schema": "S1",
                                              "physicalName": "TAB_A", "logicalName": "TAB_A"}   # recreada
    again = Migrator(db, _dos_homonimas(), "Fam", None)
    again.run()
    assert _vivo(db.data["canonical_tables"][again.pid("E2")])
    assert _tablas_vivas(db) == ["TAB_A", "TAB_A_DUP1"]
    assert again.report["deleted_in_app"] == []


def test_r18b_la_copia_dup_borrada_se_reporta_con_el_nombre_con_el_que_existio():
    db = ProjectingDb()
    first = Migrator(db, _dos_homonimas(), "Fam", None)
    first.run()
    t1, t2 = first.table_pid["E1"], first.table_pid["E2"]
    assert (db.data["canonical_tables"][t1]["physicalName"], db.data["canonical_tables"][t2]["physicalName"]) == \
        ("TAB_A", "TAB_A_DUP1")
    _borrar_tabla_en_la_app(db, t2)                                          # se borra la COPIA
    again = Migrator(db, _dos_homonimas(), "Fam", None)
    again.run()
    assert [d for d in again.report["deleted_in_app"] if "tableId" in d] == \
        [{"key": "S1.TAB_A_DUP1", "tableId": t2, "columns": 1}]
    assert _tablas_vivas(db) == ["TAB_A"]


def test_r18b_la_renombrada_en_la_app_y_borrada_se_reporta_con_su_nombre_real():
    db = ProjectingDb()
    first = Migrator(db, _solo_e1(), "Fam", None)
    first.run()
    t1 = first.table_pid["E1"]
    db.data["canonical_tables"][t1]["physicalName"] = "TAB_A_NUEVO_NOMBRE"
    _borrar_tabla_en_la_app(db, t1)
    again = Migrator(db, _solo_e1(), "Fam", None)
    again.run()
    assert again.report["deleted_in_app"] == [{"key": "S1.TAB_A_NUEVO_NOMBRE", "tableId": t1, "columns": 1}]


def test_r18b_el_crudo_no_se_usa_si_esa_clave_tiene_una_tabla_viva():
    """La borrada es de OTRO archivo (la regla del dueño la aceptaría); pero
    S1.TAB_A está VIVA (recreada en la app): la copia `_DUPn` nueva de este
    archivo no es la borrada — se migra."""
    db = ProjectingDb()
    otro = Migrator(db, _solo_e1(), "Fam", None)                             # archivo A: E1 → S1.TAB_A
    otro.run()
    _borrar_tabla_en_la_app(db, otro.table_pid["E1"])
    db.data["canonical_tables"]["t-nueva"] = {"_id": "t-nueva", "projectId": otro.project_id, "schema": "S1",
                                              "physicalName": "TAB_A", "logicalName": "TAB_A"}   # recreada
    b = ep.ErwinModel(name="Archivo B")                                       # archivo B: dos homónimas propias
    b.entities = {"EB1": entity("EB1", "TAB_A", [attr("X1", "EB1", "COD", 1)], pk=("X1",)),
                  "EB2": entity("EB2", "TAB_A", [attr("X2", "EB2", "ID", 1)], pk=("X2",))}
    b.hive_dbs = {"S1": ["EB1", "EB2"]}
    run = Migrator(db, b, "Fam", None)
    run.run()
    assert _tablas_vivas(db) == ["TAB_A", "TAB_A_DUP1"] and run.report["deleted_in_app"] == []
