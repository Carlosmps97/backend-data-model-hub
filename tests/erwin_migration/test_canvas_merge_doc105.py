"""Doc 105 — kit Erwin: canvases en la fusión R8 y en la re-corrida.

- P7: la fusión R8 conserva las vistas que el canvas ya tenía — el prefetch no
  proyectaba `viewIds` y, con una BD que respeta la proyección (Lakebase), la
  unión partía de [] — y sólo suma vistas VIVAS (existentes o creadas en la
  corrida): una vista borrada no vuelve al canvas.
- A2-o2 (decisión del owner: conservar): la fusión y la re-corrida conservan los
  dibujos del canvas hechos en la app (antes escribían `drawings: []`) y hacen
  merge de sus UDP, con los del XML encima (la re-corrida los pisaba)."""
from __future__ import annotations

import pytest

from scripts.erwin_migration import erwin_parser as ep
from scripts.erwin_migration import policies as pol
from scripts.erwin_migration.migrate import Migrator
from tests.erwin_migration.test_migrate_merge import FakeDb, base_db, modelo_archivo_2
from tests.erwin_migration.test_subcategory_projection_doc100 import ProjectingDb

DRAWING = {"id": "d1", "type": "text", "text": "nota"}


def _con_udp_de_modelo(m: ep.ErwinModel) -> ep.ErwinModel:
    """UDP «Database» de nivel Model (canvas) con valor en el XML."""
    m.udp_defs["UM"] = ep.ErwinUdpDef(id="UM", full_name="Model.Physical.Database", owner_class="Model",
                                     view_mode="Physical", short_name="Database", data_type_code="2", default="")
    m.udp_values = [*m.udp_values, ("MODEL", "Model", "UM", "DDV_PROD")]
    return m


def _database_pid(mig: Migrator) -> str:
    return mig.fixed_pid[("canvas", "physical", pol.norm_enum("Database"))]


def test_p7_fusion_conserva_las_vistas_del_canvas():
    data = base_db().data
    data["subject_areas"]["cv-1"]["viewIds"] = ["vw-0"]           # el 1er XML ya dejó una vista
    data["subject_areas"]["cv-1"]["layout"]["vw-0"] = {"x": 900, "y": 0}
    db = ProjectingDb(data)
    mig = Migrator(db, modelo_archivo_2(), "Familia DDV", None)
    mig.run()
    assert mig.stats["canvases fusionados (homónimos de otro archivo)"] == 1
    cv = db.data["subject_areas"]["cv-1"]
    assert cv["viewIds"] == ["vw-0", mig.pid("V1")]
    assert cv["layout"]["vw-0"] == {"x": 900, "y": 0}


@pytest.mark.parametrize("make_db", [FakeDb, ProjectingDb])
def test_p7_fusion_no_revive_vistas_muertas(make_db):
    data = base_db().data
    data["views"]["vw-gone"] = {"_id": "vw-gone", "schema": "S1V", "name": "BORRADA_VU",
                                "sourceTableIds": ["tbl-1"], "projectId": "proj-1", "flgactive": False}
    data["subject_areas"]["cv-1"]["viewIds"] = ["vw-0", "vw-gone", "vw-nunca-existio"]
    db = make_db(data)
    mig = Migrator(db, modelo_archivo_2(), "Familia DDV", None)
    mig.run()
    assert db.data["subject_areas"]["cv-1"]["viewIds"] == ["vw-0", mig.pid("V1")]


def test_a2o2_fusion_conserva_dibujos_y_suma_los_udp_del_xml():
    data = base_db().data
    data["subject_areas"]["cv-1"]["drawings"] = [DRAWING]
    data["subject_areas"]["cv-1"]["udpValues"] = {"u-app": "puesto en la app"}
    db = ProjectingDb(data)
    mig = Migrator(db, _con_udp_de_modelo(modelo_archivo_2()), "Familia DDV", None)
    mig.run()
    cv = db.data["subject_areas"]["cv-1"]
    assert cv["drawings"] == [DRAWING]
    assert cv["udpValues"] == {"u-app": "puesto en la app", _database_pid(mig): "DDV_PROD"}


def test_a2o2_recorrida_conserva_dibujos_y_udp_de_la_app():
    db = ProjectingDb(base_db().data)
    mig = Migrator(db, _con_udp_de_modelo(modelo_archivo_2()), "Otro", None)
    mig.run()
    cid = mig.pid("DGX")
    cv = db.data["subject_areas"][cid]
    assert cv["drawings"] == [] and cv["udpValues"] == {_database_pid(mig): "DDV_PROD"}   # canvas nuevo
    cv["drawings"] = [DRAWING]                                                              # lo hecho en la app
    cv["udpValues"] = {"u-app": "puesto en la app", _database_pid(mig): "VIEJO"}
    otra = Migrator(db, _con_udp_de_modelo(modelo_archivo_2()), "Otro", None)
    otra.run()
    assert otra.stats.get("canvases fusionados (homónimos de otro archivo)", 0) == 0     # re-corrida, no fusión
    cv = db.data["subject_areas"][cid]
    assert cv["drawings"] == [DRAWING]
    assert cv["udpValues"] == {"u-app": "puesto en la app", _database_pid(mig): "DDV_PROD"}  # el XML encima


# ── Revisión R2: re-corrida de un canvas FUSIONADO ────────────────────────
# El canvas fusionado conserva el id del archivo que lo creó: re-correr SÓLO
# ese archivo entraba por la rama «re-corrida» — miembros = los de su XML
# (salían las tablas y vistas del otro archivo aunque siguieran vivas) y layout
# sólo de sus nodos (lo trabajado a mano en los nodos del otro archivo volvía a
# la grilla). Ahora el canvas registra sus aportes (`erwinLongIds`) y, si tiene
# aportes de OTROS archivos, la re-corrida se trata como fusión.

def _archivo(sfx: str, tabla: str, *, dibuja: bool = True) -> ep.ErwinModel:
    """Un archivo de la familia: una tabla propia + una vista espejo, dibujadas
    en el diagrama «DIAG» del subject area «AREA» (mismo nombre en todos).
    `dibuja=False`: el diagrama existe pero ya no dibuja nada."""
    from tests.erwin_migration.test_migrate_merge import attr, entity
    m = ep.ErwinModel(name=f"Archivo {sfx}")
    e = entity(f"E{sfx}", tabla, [attr(f"A{sfx}", f"E{sfx}", "COD", 1)], pk=(f"A{sfx}",))
    m.entities = {e.id: e}
    m.relationships = {f"RV{sfx}": ep.ErwinRelationship(
        id=f"RV{sfx}", name="rv", rel_type=ep.REL_TABLE_TO_VIEW, cardinality="-3",
        parent_ref=f"E{sfx}", child_ref=f"V{sfx}", null_option="100")}
    m.views = {f"V{sfx}": ep.ErwinView(id=f"V{sfx}", name=f"{tabla}_VU", definition="", comment="",
                                       attributes=[attr(f"VA{sfx}", f"V{sfx}", "COD", 1, kind="View",
                                                        parent_attr=f"A{sfx}", parent_rel=f"RV{sfx}")])}
    m.hive_dbs = {"S1": [e.id], "S1V": [f"V{sfx}"]}
    m.subject_areas = [{"id": f"SA{sfx}", "name": "AREA", "definition": "", "order": 0}]
    shapes = [(e.id, None), (f"V{sfx}", None)] if dibuja else []
    m.diagrams = [ep.ErwinDiagram(id=f"DG{sfx}", name="DIAG", subject_area="AREA", owner_path="M.AREA",
                                  shapes=shapes)]
    return m


def _familia(db) -> tuple[Migrator, Migrator]:
    """Archivo 1 crea el canvas DIAG; el archivo 2 (mismo folder/nombre) se
    FUSIONA en él."""
    m1 = Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None)
    m1.run()
    m2 = Migrator(db, _archivo("2", "TAB_DOS"), "Familia", None)
    m2.run()
    assert m2.stats["canvases fusionados (homónimos de otro archivo)"] == 1
    return m1, m2


def _canvas(db) -> dict:
    (cv,) = db.data["subject_areas"].values()
    return cv


def test_r2_recorrida_del_primer_archivo_tras_la_fusion_conserva_lo_del_segundo():
    db = ProjectingDb()
    _m1, m2 = _familia(db)
    t_dos, v_dos = m2.table_pid["E2"], m2.view_pid["V2"]
    cv = _canvas(db)
    assert t_dos in cv["tableIds"] and v_dos in cv["viewIds"]                 # fusión OK
    cv["drawings"] = [DRAWING]                                                # trabajo hecho en la app
    cv["layout"][t_dos] = {"x": 7777, "y": 8888}
    otra = Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None)            # re-corrida del archivo 1
    otra.run()
    cv = _canvas(db)
    assert cv["drawings"] == [DRAWING]
    assert t_dos in cv["tableIds"], cv["tableIds"]
    assert v_dos in cv["viewIds"], cv["viewIds"]
    assert cv["layout"][t_dos] == {"x": 7777, "y": 8888}, cv["layout"][t_dos]


def test_r2_recorrida_completa_de_la_familia_conserva_el_layout_del_segundo_archivo():
    db = ProjectingDb()
    _m1, m2 = _familia(db)
    t_dos = m2.table_pid["E2"]
    _canvas(db)["layout"][t_dos] = {"x": 7777, "y": 8888}                     # arreglo a mano en la app
    Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None).run()
    Migrator(db, _archivo("2", "TAB_DOS"), "Familia", None).run()
    cv = _canvas(db)
    assert t_dos in cv["tableIds"]
    assert cv["layout"][t_dos] == {"x": 7777, "y": 8888}, cv["layout"][t_dos]


def test_r2_la_fusion_registra_los_aportes_sin_pisar_el_erwinLongId_del_creador():
    db = ProjectingDb()
    m1, m2 = _familia(db)
    cv = _canvas(db)
    assert cv["_id"] == m1.pid("DG1")
    assert cv["erwinLongId"] == "DG1", cv["erwinLongId"]                      # antes: «DG2» (el último)
    assert cv["erwinLongIds"] == ["DG1", "DG2"]
    for sfx, tabla in (("1", "TAB_UNO"), ("2", "TAB_DOS"), ("1", "TAB_UNO")):  # re-corridas: estable
        Migrator(db, _archivo(sfx, tabla), "Familia", None).run()
        cv = _canvas(db)
        assert (cv["erwinLongId"], cv["erwinLongIds"]) == ("DG1", ["DG1", "DG2"])


def test_r2_recorrida_de_canvas_fusionado_cuenta_aparte_y_no_como_fusion_nueva():
    db = ProjectingDb()
    _familia(db)
    otra = Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None)
    otra.run()
    assert otra.stats.get("canvases fusionados (homónimos de otro archivo)", 0) == 0
    assert otra.stats["canvases re-corridos como fusión (tienen aportes de otros archivos)"] == 1


def test_r2_recorrida_de_canvas_fusionado_saca_las_tablas_y_vistas_muertas():
    """La unión conserva los miembros VIVOS previos: lo borrado en la app no
    vuelve al canvas ni queda colgando (el canvas quedaba en 409 al guardar)."""
    db = ProjectingDb()
    _m1, m2 = _familia(db)
    t_dos, v_dos = m2.table_pid["E2"], m2.view_pid["V2"]
    db.data["canonical_tables"][t_dos]["flgactive"] = False                   # borradas en la app
    db.data["views"][v_dos]["flgactive"] = False
    Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None).run()
    cv = _canvas(db)
    assert t_dos not in cv["tableIds"] and v_dos not in cv["viewIds"]
    assert len(cv["tableIds"]) == 1 and len(cv["viewIds"]) == 1


def test_r2_canvas_fusionado_con_el_kit_anterior_se_reconoce_por_su_erwinLongId():
    """Canvas fusionado ANTES de `erwinLongIds`: sólo tiene `erwinLongId` (el
    último archivo que lo escribió) y distinto del diagrama que lo creó → la
    re-corrida del creador también es fusión; el creador recupera su id."""
    db = ProjectingDb()
    _m1, m2 = _familia(db)
    cv = _canvas(db)
    cv.pop("erwinLongIds", None)
    cv["erwinLongId"] = "DG2"                                                 # lo que dejaba el kit anterior
    t_dos = m2.table_pid["E2"]
    Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None).run()
    cv = _canvas(db)
    assert t_dos in cv["tableIds"]
    assert cv["erwinLongId"] == "DG1" and sorted(cv["erwinLongIds"]) == ["DG1", "DG2"]


@pytest.mark.parametrize("make_db", [FakeDb, ProjectingDb])
def test_r2_fusion_no_deja_tablas_muertas(make_db):
    """La fusión R8 filtraba las vistas muertas del canvas pero no las tablas."""
    data = base_db().data
    data["canonical_tables"]["tbl-gone"] = {"_id": "tbl-gone", "schema": "S1", "physicalName": "BORRADA",
                                            "projectId": "proj-1", "flgactive": False}
    data["subject_areas"]["cv-1"]["tableIds"] = ["tbl-1", "tbl-gone", "tbl-nunca-existio", "tbl-3"]
    db = make_db(data)
    mig = Migrator(db, modelo_archivo_2(), "Familia DDV", None)
    mig.run()
    assert db.data["subject_areas"]["cv-1"]["tableIds"] == ["tbl-1", "tbl-3", mig.pid("E4"), mig.pid("E2")]


def test_r2_recorrida_de_un_solo_archivo_sigue_mandando_el_xml():
    """Control: un canvas SIN aportes de otros archivos se re-corre como antes —
    el XML manda los miembros (lo que el diagrama dejó de dibujar, sale)."""
    db = ProjectingDb()
    m1 = Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None)
    m1.run()
    t_uno = m1.table_pid["E1"]
    assert _canvas(db)["tableIds"] == [t_uno] and _canvas(db)["erwinLongIds"] == ["DG1"]
    otra = Migrator(db, _archivo("1", "TAB_UNO", dibuja=False), "Familia", None)
    otra.run()
    cv = _canvas(db)
    assert cv["tableIds"] == [] and cv["viewIds"] == []
    assert otra.stats.get("canvases re-corridos como fusión (tienen aportes de otros archivos)", 0) == 0


def test_r2_mismo_diagrama_en_dos_archivos_de_la_familia_sigue_siendo_recorrida():
    """Control: copias de la familia con el MISMO Long_Id de diagrama caen en
    el mismo canvas por id — es una re-corrida (gana el último archivo), no una
    fusión: el aporte es el mismo diagrama."""
    db = ProjectingDb()
    Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None).run()
    copia = _archivo("2", "TAB_DOS")
    copia.diagrams[0].id = "DG1"                                              # mismo Long_Id
    mig = Migrator(db, copia, "Familia", None)
    mig.run()
    cv = _canvas(db)
    assert cv["tableIds"] == [mig.table_pid["E2"]] and cv["erwinLongIds"] == ["DG1"]


# ── Revisión R5: lo borrado en la app y los símbolos de subcategoría ───────
# El kit no revive lo borrado en la app (`flgactive` sólo al insertar), pero
# si el XML todavía lo dibuja su id volvía al canvas (colgando: todo guardado
# del canvas daba 409), en la re-corrida «el XML manda» y en la fusión. Y la
# re-corrida «el XML manda» armaba el layout sólo con nodos del XML: se perdía
# la posición de los símbolos de subcategoría acomodados en la app.

def _borrar_en_la_app(db, table_id: str, view_id: str) -> None:
    """Lo que deja un publish que borra la tabla y su vista: ambas inactivas y
    fuera de los canvases (la cascada del front las saca del canvas)."""
    db.data["canonical_tables"][table_id]["flgactive"] = False
    db.data["views"][view_id]["flgactive"] = False
    for cv in db.data["subject_areas"].values():
        cv["tableIds"] = [t for t in cv.get("tableIds") or [] if t != table_id]
        cv["viewIds"] = [v for v in cv.get("viewIds") or [] if v != view_id]
        for k in (table_id, view_id):
            (cv.get("layout") or {}).pop(k, None)


def test_r5_recorrida_del_mismo_archivo_no_deja_colgando_lo_borrado_en_la_app():
    db = ProjectingDb()
    m1 = Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None)
    m1.run()
    t1, v1 = m1.table_pid["E1"], m1.view_pid["V1"]
    _borrar_en_la_app(db, t1, v1)
    Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None).run()
    assert db.data["canonical_tables"][t1]["flgactive"] is False            # no revive (lo decide el owner)
    assert db.data["views"][v1]["flgactive"] is False
    cv = _canvas(db)
    assert (cv["tableIds"], cv["viewIds"]) == ([], []) and t1 not in cv["layout"] and v1 not in cv["layout"]


def test_r5_recorrida_como_fusion_no_deja_colgando_lo_borrado_en_la_app():
    db = ProjectingDb()
    m1, m2 = _familia(db)
    t2, v2 = m2.table_pid["E2"], m2.view_pid["V2"]
    _borrar_en_la_app(db, t2, v2)
    Migrator(db, _archivo("2", "TAB_DOS"), "Familia", None).run()           # re-corrida del archivo 2
    cv = _canvas(db)
    assert cv["tableIds"] == [m1.table_pid["E1"]] and cv["viewIds"] == [m1.view_pid["V1"]]
    assert t2 not in cv["layout"] and v2 not in cv["layout"]


def _sin_marcas(data: dict) -> dict:
    import copy
    out = copy.deepcopy(data)
    for docs in out.values():
        for d in docs.values():
            d.pop("updatedAt", None)
            d.pop("createdAt", None)
    return out


def test_r5_control_dos_corridas_seguidas_de_la_familia_no_cambian_nada():
    db = ProjectingDb()
    _familia(db)
    antes = _sin_marcas(db.data)
    Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None).run()
    Migrator(db, _archivo("2", "TAB_DOS"), "Familia", None).run()
    assert _sin_marcas(db.data) == antes


def _con_subcategoria() -> ep.ErwinModel:
    from tests.erwin_migration.test_subcategory_projection_doc100 import _modelo_subtipos
    m = _modelo_subtipos()
    m.subject_areas = [{"id": "SA1", "name": "AREA", "definition": "", "order": 0}]
    m.diagrams = [ep.ErwinDiagram(id="DG1", name="DIAG", subject_area="AREA", owner_path="M.AREA",
                                  shapes=[("E1", None), ("E2", None), ("E3", None)])]
    return m


def test_r5_recorrida_del_mismo_archivo_conserva_la_posicion_de_los_simbolos_de_subcategoria():
    db = ProjectingDb()
    Migrator(db, _con_subcategoria(), "Fam", None).run()
    (sym,) = {r["subtypeSymbolId"] for r in db.data["relationships"].values() if r.get("subtypeSymbolId")}
    cv = _canvas(db)
    cv["layout"][sym] = {"x": 1234, "y": 567}                               # acomodado en la app
    cv["layout"]["nodo-que-ya-no-existe"] = {"x": 1, "y": 1}                 # esto sí lo limpia el XML
    Migrator(db, _con_subcategoria(), "Fam", None).run()
    layout = _canvas(db)["layout"]
    assert layout.get(sym) == {"x": 1234, "y": 567}, layout
    assert "nodo-que-ya-no-existe" not in layout


def test_r5_vista_borrada_con_su_tabla_viva_no_vuelve_al_canvas():
    """Sólo la vista se borró en la app: el XML la re-escribe MUERTA (no revive)
    y su id no debe volver al canvas (la tabla, viva, sí sigue)."""
    db = ProjectingDb()
    m1 = Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None)
    m1.run()
    t1, v1 = m1.table_pid["E1"], m1.view_pid["V1"]
    db.data["views"][v1]["flgactive"] = False
    for cv in db.data["subject_areas"].values():
        cv["viewIds"] = [v for v in cv.get("viewIds") or [] if v != v1]
        (cv.get("layout") or {}).pop(v1, None)
    Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None).run()
    cv = _canvas(db)
    assert db.data["views"][v1]["flgactive"] is False
    assert (cv["tableIds"], cv["viewIds"]) == ([t1], []) and v1 not in cv["layout"]
