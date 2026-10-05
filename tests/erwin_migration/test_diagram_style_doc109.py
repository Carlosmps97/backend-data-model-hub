"""Doc 109 — colores, textos, dibujos y posiciones de Erwin en el kit.

Fixture propio, chico y explícito: themes del BCP + los dos de Erwin («Classic»
blanco y «Default», el del modelo), una tabla con theme propio, otra con color a
mano en la caja, una vista, un texto, un marco con título que encierra las dos
tablas, una línea, y un SEGUNDO diagrama donde la caja de la primera tabla lleva
otro theme y una tercera tabla se le encima.
"""
from __future__ import annotations

import textwrap

import pytest

from scripts.arrange_all import table_size
from scripts.erwin_migration import colors as col
from scripts.erwin_migration import erwin_parser as ep
from scripts.erwin_migration import layout as lay
from scripts.erwin_migration.migrate import SYMBOL_SIZE, Migrator
from tests.erwin_migration.test_migrate_merge import FakeDb

YELLOW, BLUE, GREEN, DEFAULT, WHITE = 8454143, 16753536, 5296274, 16767178, 16777215

FIXTURE = textwrap.dedent(f"""\
<?xml version="1.0" encoding="UTF-8"?>
<erwin xmlns="http://www.erwin.com/dm" FileVersion="10.10" Format="erwin">
 <Model xmlns="http://www.erwin.com/dm/data" id="M1" name="Modelo Estilos">
  <ModelProps><Name>Modelo Estilos</Name><Theme_Ref>TH_DEF</Theme_Ref></ModelProps>
  <Theme_Groups>
   <Theme id="TH_CLA" name="Classic Theme"><ThemeProps>
    <Entity_Fill_Color1>{WHITE}</Entity_Fill_Color1><Entity_Fill_Color2>{WHITE}</Entity_Fill_Color2>
    <Entity_Fill_Style>0</Entity_Fill_Style></ThemeProps></Theme>
   <Theme id="TH_DEF" name="Default Theme"><ThemeProps>
    <Entity_Fill_Color1>{WHITE}</Entity_Fill_Color1><Entity_Fill_Color2>{DEFAULT}</Entity_Fill_Color2>
    <Entity_Fill_Style>4</Entity_Fill_Style>
    <View_Fill_Color1>{WHITE}</View_Fill_Color1><View_Fill_Color2>{DEFAULT}</View_Fill_Color2>
    <View_Fill_Style>4</View_Fill_Style></ThemeProps></Theme>
   <Theme id="TH_PRI" name="Entidad Principal"><ThemeProps>
    <Entity_Fill_Color1>{WHITE}</Entity_Fill_Color1><Entity_Fill_Color2>{YELLOW}</Entity_Fill_Color2>
    <Entity_Fill_Style>4</Entity_Fill_Style>
    <View_Fill_Color1>{WHITE}</View_Fill_Color1><View_Fill_Color2>{DEFAULT}</View_Fill_Color2>
    <View_Fill_Style>4</View_Fill_Style></ThemeProps></Theme>
   <Theme id="TH_SEC" name="Entidad Secundaria"><ThemeProps>
    <Entity_Fill_Color1>{WHITE}</Entity_Fill_Color1><Entity_Fill_Color2>{BLUE}</Entity_Fill_Color2>
    <Entity_Fill_Style>4</Entity_Fill_Style></ThemeProps></Theme>
  </Theme_Groups>
  <Entity_Groups>
   <Entity id="E1" name="Tabla Uno">
    <EntityProps><Name>Tabla Uno</Name><Physical_Name>TAB_UNO</Physical_Name>
     <User_Formatted_Physical_Name>TAB_UNO</User_Formatted_Physical_Name><Theme_Ref>TH_PRI</Theme_Ref></EntityProps>
    <Attribute_Groups>
     <Attribute id="A1" name="codigo"><AttributeProps><Name>codigo</Name>
      <User_Formatted_Physical_Name>COD</User_Formatted_Physical_Name>
      <Physical_Data_Type>VARCHAR(20)</Physical_Data_Type><Physical_Order>1</Physical_Order></AttributeProps></Attribute>
     <Attribute id="A2" name="nombre"><AttributeProps><Name>nombre</Name>
      <User_Formatted_Physical_Name>NOMBRE</User_Formatted_Physical_Name>
      <Physical_Data_Type>VARCHAR(100)</Physical_Data_Type><Physical_Order>2</Physical_Order></AttributeProps></Attribute>
    </Attribute_Groups>
   </Entity>
   <Entity id="E2" name="Tabla Dos">
    <EntityProps><Name>Tabla Dos</Name><Physical_Name>TAB_DOS</Physical_Name>
     <User_Formatted_Physical_Name>TAB_DOS</User_Formatted_Physical_Name></EntityProps>
    <Attribute_Groups>
     <Attribute id="B1" name="codigo"><AttributeProps><Name>codigo</Name>
      <User_Formatted_Physical_Name>COD</User_Formatted_Physical_Name>
      <Physical_Data_Type>VARCHAR(20)</Physical_Data_Type><Physical_Order>1</Physical_Order></AttributeProps></Attribute>
    </Attribute_Groups>
   </Entity>
   <Entity id="E3" name="Tabla Tres">
    <EntityProps><Name>Tabla Tres</Name><Physical_Name>TAB_TRES</Physical_Name>
     <User_Formatted_Physical_Name>TAB_TRES</User_Formatted_Physical_Name></EntityProps>
    <Attribute_Groups>
     <Attribute id="C1" name="codigo"><AttributeProps><Name>codigo</Name>
      <User_Formatted_Physical_Name>COD</User_Formatted_Physical_Name>
      <Physical_Data_Type>VARCHAR(20)</Physical_Data_Type><Physical_Order>1</Physical_Order></AttributeProps></Attribute>
    </Attribute_Groups>
   </Entity>
  </Entity_Groups>
  <View_Groups>
   <View id="V1" name="V_UNO"><ViewProps><Name>V_UNO</Name></ViewProps>
    <Attribute_Groups>
     <Attribute id="VA1" name="COD"><AttributeProps><Name>COD</Name>
      <User_Formatted_Physical_Name>COD</User_Formatted_Physical_Name>
      <Physical_Data_Type>VARCHAR(20)</Physical_Data_Type>
      <Parent_Attribute_Ref>A1</Parent_Attribute_Ref></AttributeProps></Attribute>
    </Attribute_Groups>
   </View>
  </View_Groups>
  <Relationship_Groups>
   <Relationship id="R16" name="R/16"><RelationshipProps><Type>16</Type>
    <Parent_Entity_Ref>E1</Parent_Entity_Ref><Child_Entity_Ref>V1</Child_Entity_Ref></RelationshipProps></Relationship>
  </Relationship_Groups>
  <Annotation_Groups>
   <Annotation id="AN1" name="Annotation_1"><AnnotationProps><Type>0</Type><Text>TITULO
 DEL AREA</Text></AnnotationProps></Annotation>
  </Annotation_Groups>
  <Subject_Area_Groups>
   <Subject_Area id="SA1" name="Area"><Subject_AreaProps><Name>Area</Name></Subject_AreaProps>
    <ER_Diagram_Groups>
     <ER_Diagram id="D1" name="Diagrama Uno"><ER_DiagramProps><Name>Diagrama Uno</Name>
      <Owner_Path>Modelo Estilos.Area</Owner_Path></ER_DiagramProps>
      <ER_Model_Shape_Groups>
       <ER_Model_Shape id="S1"><ER_Model_ShapeProps><Model_Object_Ref>E1</Model_Object_Ref>
        <Anchor_Point>0 0</Anchor_Point><Owner_Path>Modelo Estilos.Area.Diagrama Uno</Owner_Path>
        <Entity_Fill_Color1 Derived="Y">{WHITE}</Entity_Fill_Color1>
        <Entity_Fill_Color2 Derived="Y">{YELLOW}</Entity_Fill_Color2>
        <Entity_Fill_Style Derived="Y">4</Entity_Fill_Style></ER_Model_ShapeProps></ER_Model_Shape>
       <ER_Model_Shape id="S2"><ER_Model_ShapeProps><Model_Object_Ref>E2</Model_Object_Ref>
        <Anchor_Point>300 0</Anchor_Point><Owner_Path>Modelo Estilos.Area.Diagrama Uno</Owner_Path>
        <Entity_Fill_Color1 Derived="Y">{WHITE}</Entity_Fill_Color1>
        <Entity_Fill_Color2>{GREEN}</Entity_Fill_Color2>
        <Entity_Fill_Style Derived="Y">4</Entity_Fill_Style></ER_Model_ShapeProps></ER_Model_Shape>
       <ER_Model_Shape id="S3"><ER_Model_ShapeProps><Model_Object_Ref>V1</Model_Object_Ref>
        <Anchor_Point>0 -300</Anchor_Point><Owner_Path>Modelo Estilos.Area.Diagrama Uno</Owner_Path>
        <View_Fill_Color1 Derived="Y">{WHITE}</View_Fill_Color1>
        <View_Fill_Color2 Derived="Y">{DEFAULT}</View_Fill_Color2>
        <View_Fill_Style Derived="Y">4</View_Fill_Style></ER_Model_ShapeProps></ER_Model_Shape>
       <ER_Model_Shape id="S4"><ER_Model_ShapeProps><Model_Object_Ref>AN1</Model_Object_Ref>
        <Anchor_Point>0 150</Anchor_Point><Fixed_Size_Point>200 30</Fixed_Size_Point>
        <Owner_Path>Modelo Estilos.Area.Diagrama Uno</Owner_Path>
        <Annotation_Font_Name Derived="Y">Microsoft Sans Serif</Annotation_Font_Name>
        <Annotation_Font_Size>20</Annotation_Font_Size>
        <Annotation_Is_Font_Bold>true</Annotation_Is_Font_Bold>
        <Annotation_Font_Underscore>true</Annotation_Font_Underscore>
        <Annotation_Font_Color Derived="Y">0</Annotation_Font_Color>
        <Annotation_Fill_Color1 Derived="Y">{WHITE}</Annotation_Fill_Color1>
        <Annotation_Fill_Color2 Derived="Y">{DEFAULT}</Annotation_Fill_Color2>
        <Annotation_Fill_Style Derived="Y">4</Annotation_Fill_Style>
        <Annotation_Outline_Color Derived="Y">10066329</Annotation_Outline_Color>
        <Annotation_Text_Horizontal_Alignment Derived="Y">1</Annotation_Text_Horizontal_Alignment>
        <Annotation_Text_Vertical_Alignment Derived="Y">2</Annotation_Text_Vertical_Alignment>
       </ER_Model_ShapeProps></ER_Model_Shape>
      </ER_Model_Shape_Groups>
      <Shape_Groups>
       <Shape id="F1" name="Shape_1"><ShapeProps><Type>0</Type><Text>MARCO</Text>
        <Anchor_Point>150 0</Anchor_Point><Fixed_Size_Point>500 200</Fixed_Size_Point>
        <Owner_Path>Modelo Estilos.Area.Diagrama Uno</Owner_Path>
        <Annotation_Font_Size>16</Annotation_Font_Size>
        <Annotation_Fill_Color1 Derived="Y">{WHITE}</Annotation_Fill_Color1>
        <Annotation_Fill_Color2>{GREEN}</Annotation_Fill_Color2>
        <Annotation_Fill_Style Derived="Y">4</Annotation_Fill_Style>
        <Annotation_Outline_Color Derived="Y">0</Annotation_Outline_Color>
        <Annotation_Text_Horizontal_Alignment Derived="Y">1</Annotation_Text_Horizontal_Alignment>
        <Annotation_Text_Vertical_Alignment Derived="Y">2</Annotation_Text_Vertical_Alignment>
       </ShapeProps></Shape>
       <Shape id="L1" name="Shape_2"><ShapeProps><Type>15</Type>
        <Anchor_Point>-100 -200</Anchor_Point><Anchor_Point_2>100 -200</Anchor_Point_2>
        <Owner_Path>Modelo Estilos.Area.Diagrama Uno</Owner_Path>
        <Annotation_Line_Color>255</Annotation_Line_Color></ShapeProps></Shape>
      </Shape_Groups>
     </ER_Diagram>
     <ER_Diagram id="D2" name="Diagrama Dos"><ER_DiagramProps><Name>Diagrama Dos</Name>
      <Owner_Path>Modelo Estilos.Area</Owner_Path></ER_DiagramProps>
      <ER_Model_Shape_Groups>
       <ER_Model_Shape id="T1"><ER_Model_ShapeProps><Model_Object_Ref>E1</Model_Object_Ref>
        <Anchor_Point>0 0</Anchor_Point><Owner_Path>Modelo Estilos.Area.Diagrama Dos</Owner_Path>
        <Theme_Ref>TH_SEC</Theme_Ref>
        <Entity_Fill_Color1 Derived="Y">{WHITE}</Entity_Fill_Color1>
        <Entity_Fill_Color2 Derived="Y">{BLUE}</Entity_Fill_Color2>
        <Entity_Fill_Style Derived="Y">4</Entity_Fill_Style></ER_Model_ShapeProps></ER_Model_Shape>
       <ER_Model_Shape id="T3"><ER_Model_ShapeProps><Model_Object_Ref>E3</Model_Object_Ref>
        <Anchor_Point>20 10</Anchor_Point><Owner_Path>Modelo Estilos.Area.Diagrama Dos</Owner_Path>
        <Entity_Fill_Color1 Derived="Y">{WHITE}</Entity_Fill_Color1>
        <Entity_Fill_Color2 Derived="Y">{DEFAULT}</Entity_Fill_Color2>
        <Entity_Fill_Style Derived="Y">4</Entity_Fill_Style></ER_Model_ShapeProps></ER_Model_Shape>
      </ER_Model_Shape_Groups>
     </ER_Diagram>
    </ER_Diagram_Groups>
   </Subject_Area>
  </Subject_Area_Groups>
 </Model>
</erwin>
""")


@pytest.fixture
def model(tmp_path) -> ep.ErwinModel:
    p = tmp_path / "estilos.xml"
    p.write_text(FIXTURE, encoding="utf-8")
    return ep.parse(str(p))


def _diagram(m: ep.ErwinModel, name: str) -> ep.ErwinDiagram:
    return next(d for d in m.diagrams if d.name == name)


# ── parser ──────────────────────────────────────────────────────────────────


def test_colorref_es_bgr_y_el_relleno_visible_depende_del_estilo():
    assert ep.colorref_hex(str(YELLOW)) == "#FFFF80"
    assert ep.colorref_hex(str(BLUE)) == "#80A3FF"
    assert ep.colorref_hex("abc") is None and ep.colorref_hex("-1") is None
    assert ep.visible_fill("4", str(WHITE), str(GREEN)) == "#92D050"     # degradado → Color2
    # sólido → también Color2 (el modelador que pinta una caja sólida cambia
    # Color2 y deja Color1 blanco heredado); sin Color2, Color1
    assert ep.visible_fill("0", str(WHITE), str(GREEN)) == "#92D050"
    assert ep.visible_fill("0", str(WHITE), None) == "#FFFFFF"


def test_parser_lee_themes_theme_de_la_tabla_y_cajas_con_su_estilo(model):
    assert model.model_theme_ref == "TH_DEF"
    assert {t.name: t.entity_fill for t in model.themes.values()} == {
        "Classic Theme": "#FFFFFF", "Default Theme": "#CAD8FF",
        "Entidad Principal": "#FFFF80", "Entidad Secundaria": "#80A3FF"}
    assert model.entities["E1"].theme_ref == "TH_PRI" and model.entities["E2"].theme_ref is None
    d1 = _diagram(model, "Diagrama Uno")
    boxes = {b.ref: b for b in d1.boxes}
    assert boxes["E1"].center == (0, 0) and boxes["E1"].fill == "#FFFF80" and not boxes["E1"].fill_explicit
    assert boxes["E2"].fill == "#92D050" and boxes["E2"].fill_explicit       # color a mano en la caja
    assert boxes["V1"].fill == "#CAD8FF"
    assert _diagram(model, "Diagrama Dos").boxes[0].theme_ref == "TH_SEC"
    # los refs siguen llegando como siempre (usados por canvases/score)
    assert [r for r, _a in d1.shapes] == ["E1", "E2", "V1", "AN1"]


def test_parser_lee_textos_y_dibujos_con_su_formato(model):
    d1 = _diagram(model, "Diagrama Uno")
    text = next(b for b in d1.boxes if b.ref == "AN1")
    assert model.annotation_text["AN1"] == "TITULO\n DEL AREA"
    assert text.size == (200, 30) and text.center == (0, 150)
    s = text.text
    assert (s.font, s.size, s.bold, s.italic, s.underline) == ("Microsoft Sans Serif", 20, True, False, True)
    assert (s.color, s.fill, s.outline, s.align, s.valign) == ("#000000", "#CAD8FF", "#999999", "center", "top")
    frame, line = d1.drawings
    assert (frame.kind, frame.text, frame.size, frame.style.fill) == ("0", "MARCO", (500, 200), "#92D050")
    assert (line.kind, line.center, line.end, line.style.line) == ("15", (-100, -200), (100, -200), "#FF0000")


def test_parse_cached_reusa_el_modelo_y_cambia_con_el_archivo(tmp_path):
    xml = tmp_path / "m.xml"
    xml.write_text(FIXTURE, encoding="utf-8")
    cache = tmp_path / "cache"
    a = ep.parse_cached(str(xml), str(cache))
    assert len(list(cache.glob("*.pkl"))) == 1
    b = ep.parse_cached(str(xml), str(cache))
    assert b.entities.keys() == a.entities.keys() and b.themes.keys() == a.themes.keys()
    xml.write_text(FIXTURE.replace("Tabla Uno", "Tabla Uno Bis"), encoding="utf-8")
    c = ep.parse_cached(str(xml), str(cache))
    assert c.entities["E1"].name == "Tabla Uno Bis"          # otro contenido → otra clave
    assert ep.parse_cached(str(xml), None).entities["E1"].name == "Tabla Uno Bis"


# ── colores ─────────────────────────────────────────────────────────────────


def test_el_look_base_de_erwin_no_es_theme(model):
    assert col.neutral_fills(model) == {"#FFFFFF", "#CAD8FF"}
    assert [t.name for t in col.colored_themes(model)] == ["Entidad Principal", "Entidad Secundaria"]


def test_color_de_tabla_y_de_caja_como_en_erwin(model):
    app = {"TH_PRI": "t-pri", "TH_SEC": "t-sec"}
    neutral = col.neutral_fills(model)
    assert col.object_color(model, "TH_PRI", "Entity", app) == "theme:t-pri"
    assert col.object_color(model, None, "Entity", app) is None
    assert col.object_color(model, "TH_DEF", "Entity", app) is None       # theme base = sin color
    d1, d2 = _diagram(model, "Diagrama Uno"), _diagram(model, "Diagrama Dos")
    b = {x.ref: x for x in d1.boxes}
    chain = lambda ref: (getattr(model.entities.get(ref) or model.views.get(ref), "theme_ref", None),
                         None, None, model.model_theme_ref)
    # hereda el theme de la tabla → misma referencia, sin excepción en el canvas
    e1 = col.box_color(model, b["E1"], "Entity", chain("E1"), app, neutral)
    assert e1 == "theme:t-pri" and col.canvas_override(e1, "theme:t-pri") is None
    # color a mano en la caja → color fijo como excepción
    e2 = col.box_color(model, b["E2"], "Entity", chain("E2"), app, neutral)
    assert e2 == "#92D050" and col.canvas_override(e2, None) == "#92D050"
    # vista con el look base → sin color
    assert col.box_color(model, b["V1"], "View", chain("V1"), app, neutral) is None
    # otro theme puesto a la CAJA en el segundo diagrama → excepción a ese theme
    t1 = next(x for x in d2.boxes if x.ref == "E1")
    assert col.canvas_override(col.box_color(model, t1, "Entity", chain("E1"), app, neutral),
                               "theme:t-pri") == "theme:t-sec"
    # caja sin color sobre una tabla con color → «none» explícito
    assert col.canvas_override(None, "theme:t-pri") == "none"


# ── layout ──────────────────────────────────────────────────────────────────


def test_tamano_de_bloque_es_el_mismo_que_el_del_auto_arrange():
    """Contrato: la migración mide los bloques EXACTAMENTE como arrange_all
    (que es espejo de lodSize.ts / exportScene.ts del front)."""
    cols = [("CODCLAVECIC", "codigo clave cic", "VARCHAR(20)"), ("NBR", "nombre del cliente completo", "STRING")]
    dims = {"t": (2, max(len("CODCLAVECIC") + 11, len("nombre del cliente completo") + 6))}
    meta = {"t": ("UDV", "M_CLIENTE", "Cliente")}
    assert lay.table_box("UDV", "M_CLIENTE", "Cliente", cols) == table_size(dims, meta, "t")
    assert lay.table_box("", "X", "", []) == (240, 60)          # mínimo: 1 fila
    assert lay.table_box("S", "A" * 200, "", [])[0] == 880       # tope de ancho


def test_separar_no_deja_solapes_y_mueve_lo_minimo():
    rects = {"a": [0, 0, 240, 100], "b": [100, 20, 240, 100], "c": [2000, 0, 240, 100]}
    out = lay.separate(rects)
    assert lay.overlaps(out, lay.GAP - 1) == 0
    assert out["c"] == rects["c"]                                   # el que no choca no se mueve
    assert out == lay.separate(rects)                               # determinista
    assert rects["a"] == [0, 0, 240, 100]                           # no muta la entrada


def test_separar_respeta_lo_fijo():
    rects = {"viejo": [0, 0, 240, 100], "nuevo": [10, 10, 240, 100]}
    out = lay.separate(rects, pinned={"viejo"})
    assert out["viejo"] == [0, 0, 240, 100]
    assert lay.overlaps(out, lay.GAP - 1) == 0


def test_separar_canvas_denso_queda_limpio():
    rects = {f"t{i}": [(i % 5) * 50.0, (i // 5) * 40.0, 240.0, 88.0] for i in range(25)}
    assert lay.overlaps(lay.separate(rects), lay.GAP - 1) == 0


def test_marco_crece_lo_justo_y_fusion_se_ubica_a_la_derecha():
    assert lay.grow_frame([0, 0, 100, 100], [[10, 10, 20, 20]]) == [0, 0, 100, 100]
    assert lay.grow_frame([0, 0, 100, 100], [[90, 10, 50, 20]], pad=10) == [0, 0, 150, 100]
    moved = lay.place_beside({"n": [0, 0, 100, 50]}, {"v": [0, 300, 400, 100]}, gap=200)
    assert moved["n"][:2] == [600, 300]


# ── migrate (BD falsa) ──────────────────────────────────────────────────────


def _run(db, model) -> Migrator:
    mig = Migrator(db, model, "Proyecto Estilos", None, source_folder="Origen")
    mig.run()
    return mig


def _canvas(db, name: str) -> dict:
    return next(c for c in db.data["subject_areas"].values() if c["name"] == name)


def test_migrate_siembra_themes_y_colores_como_en_erwin(model):
    db = FakeDb()
    mig = _run(db, model)
    themes = {t["name"]: t for t in db.data["diagram_themes"].values()}
    assert {n: t["color"] for n, t in themes.items()} == {"Entidad Principal": "#FFFF80",
                                                          "Entidad Secundaria": "#80A3FF"}
    assert all(t["projectId"] == mig.project_id for t in themes.values())
    tables = {t["physicalName"]: t for t in db.data["canonical_tables"].values()}
    pri, sec = themes["Entidad Principal"]["_id"], themes["Entidad Secundaria"]["_id"]
    assert tables["TAB_UNO"]["color"] == f"theme:{pri}"
    assert tables["TAB_DOS"]["color"] is None
    view = next(iter(db.data["views"].values()))
    assert view["color"] is None
    d1, d2 = _canvas(db, "Diagrama Uno"), _canvas(db, "Diagrama Dos")
    assert d1["colors"] == {tables["TAB_DOS"]["_id"]: "#92D050"}
    assert d2["colors"] == {tables["TAB_UNO"]["_id"]: f"theme:{sec}"}


def test_migrate_ubica_con_la_escala_de_erwin_y_separa_lo_que_se_pisa(model):
    db = FakeDb()
    _run(db, model)
    tables = {t["physicalName"]: t["_id"] for t in db.data["canonical_tables"].values()}
    d1 = _canvas(db, "Diagrama Uno")
    w, h = lay.table_box("No_Definido", "TAB_UNO", "Tabla Uno",
                         [("COD", "codigo", "VARCHAR(20)"), ("NOMBRE", "nombre", "VARCHAR(100)")])
    # centro de Erwin (0, 0) × escala − medio bloque: posición entera
    assert d1["layout"][tables["TAB_UNO"]] == {"x": round(-w / 2), "y": round(-h / 2)}
    w2, h2 = lay.table_box("No_Definido", "TAB_DOS", "Tabla Dos", [("COD", "codigo", "VARCHAR(20)")])
    assert d1["layout"][tables["TAB_DOS"]] == {"x": round(300 * lay.SCALE - w2 / 2), "y": round(-h2 / 2)}
    # en el segundo diagrama TAB_TRES caía encima de TAB_UNO: quedan separadas
    d2 = _canvas(db, "Diagrama Dos")
    sizes = {tables["TAB_UNO"]: (w, h), tables["TAB_TRES"]: (w2, h2)}
    rects = {k: [p["x"], p["y"], *sizes[k]] for k, p in d2["layout"].items()}
    assert lay.overlaps(rects, lay.GAP - 2) == 0


def test_migrate_trae_textos_y_cuadros_con_su_formato(model):
    db = FakeDb()
    _run(db, model)
    d1 = _canvas(db, "Diagrama Uno")
    by_type = {d["type"]: d for d in d1["drawings"]}
    text, frame, line = by_type["text"], by_type["rect"], by_type["line"]
    assert text["text"] == "TITULO\n DEL AREA"
    assert text["font"] == {"family": "Microsoft Sans Serif", "size": 20, "bold": True,
                            "italic": False, "underline": True, "color": "#000000"}
    assert (text["fill"], text["color"], text["align"], text["valign"]) == ("#FFFFFF", "#999999", "center", "top")
    assert (text["w"], text["h"]) == (round(200 * lay.SCALE), round(30 * lay.SCALE))
    assert (frame["text"], frame["fill"], frame["valign"]) == ("MARCO", "#92D050", "top")
    # el marco sigue encerrando sus dos tablas
    tables = {t["physicalName"]: t["_id"] for t in db.data["canonical_tables"].values()}
    for name, cols in (("TAB_UNO", 2), ("TAB_DOS", 1)):
        p = d1["layout"][tables[name]]
        assert frame["x"] <= p["x"] and frame["y"] <= p["y"]
        assert p["x"] + 240 <= frame["x"] + frame["w"]
        assert p["y"] + 32 + cols * 28 <= frame["y"] + frame["h"]
    assert line["color"] == "#FF0000" and line["w"] > line["h"]
    ids = [d["id"] for d in d1["drawings"]]
    assert len(ids) == len(set(ids)) == 3


def test_recorrer_no_mueve_ni_duplica_y_conserva_lo_hecho_en_la_app(model):
    db = FakeDb()
    _run(db, model)
    d1 = _canvas(db, "Diagrama Uno")
    tables = {t["physicalName"]: t["_id"] for t in db.data["canonical_tables"].values()}
    # en la app: se mueve una tabla, se edita un texto y se cambia un color
    d1["layout"][tables["TAB_UNO"]] = {"x": 5000, "y": 5000}
    d1["drawings"][0]["text"] = "editado en la app"
    d1["colors"][tables["TAB_DOS"]] = "#FF0000"
    _run(db, model)
    again = _canvas(db, "Diagrama Uno")
    assert again["layout"][tables["TAB_UNO"]] == {"x": 5000, "y": 5000}
    assert again["drawings"][0]["text"] == "editado en la app"
    assert len(again["drawings"]) == 3
    assert again["colors"][tables["TAB_DOS"]] == "#FF0000"


def test_simbolo_de_subcategoria_mide_lo_del_canvas():
    assert SYMBOL_SIZE == 28        # espejo de subtypeGraph.ts (SYMBOL_SIZE)


# ── revisión del kit (doc 109) ──────────────────────────────────────────────

_FAMILY_IDS = ("E1", "E2", "E3", "D1", "D2", "SA1", "V1", "A1", "A2", "B1", "C1", "VA1", "R16", "AN1",
               "F1", "L1", "S1", "S2", "S3", "S4", "T1", "T3")


def _family_file(xml: str, tag: str, rename_tables: bool = False) -> str:
    """El mismo modelo como OTRO archivo de la familia: otros ids de Erwin
    (mismos nombres de subject area y diagramas: sus canvases se fusionan)."""
    for old in _FAMILY_IDS:
        xml = xml.replace(f'"{old}"', f'"{tag}{old}"').replace(f">{old}<", f">{tag}{old}<")
    if rename_tables:
        for t in ("TAB_UNO", "TAB_DOS", "TAB_TRES", "V_UNO"):
            xml = xml.replace(t, f"{t}_{tag}")
    return xml


def _parse(tmp_path, xml: str, name: str) -> ep.ErwinModel:
    p = tmp_path / name
    p.write_text(xml, encoding="utf-8")
    return ep.parse(str(p))


def test_vista_y_tabla_miden_como_la_app_con_el_tipo_plegado():
    """Contrato con `viewLodSize`/`tableLodSize` (lodSize.ts): fila = nombre +
    tipo (complejos plegados a `X<…>`), ancho = max(header, fila + 6 | + 3)."""
    assert lay.collapse_complex_type("array<struct<a:int>>") == "ARRAY<…>"
    assert lay.collapse_complex_type(" DECIMAL(10,2) ") == "DECIMAL(10,2)"
    assert lay.view_box("S", "V", [("CODIGO_CLIENTE", "VARCHAR(20)")]) == (lay.box_width(14 + 11 + 6), 60)
    assert lay.view_box("", "V", [("C", "ARRAY<STRUCT<a:INT,b:STRING>>")]) == (lay.box_width(12 + 6), 60)
    assert lay.view_box("", "V", []) == (240, 60)
    assert lay.table_box("", "T", "", [("C", "c", "STRUCT<x:INT,y:STRING>")])[0] == lay.box_width(12 + 3)


def test_vista_de_la_migracion_mide_con_los_tipos_de_sus_columnas(model):
    db = FakeDb()
    mig = _run(db, model)
    view = next(iter(db.data["views"].values()))
    # V_UNO.COD viene de TAB_UNO.COD (VARCHAR(20)): mide nombre + tipo
    assert mig.box_size[view["_id"]] == lay.view_box("No_Definido", "V_UNO", [("COD", "VARCHAR(20)")])


def test_separar_nunca_deja_solapes_aunque_no_converja(monkeypatch):
    monkeypatch.setattr(lay, "_MAX_ROUNDS", 2)          # cajas apiladas: no alcanza
    rects = {f"t{i:02d}": [0.0, 0.0, 240.0, 120.0] for i in range(25)}
    rects["fijo"] = [0.0, 0.0, 300.0, 300.0]
    aside: list[str] = []
    out = lay.separate(rects, pinned={"fijo"}, set_aside=aside)
    assert aside and "fijo" not in aside
    assert lay.overlaps(out, lay.GAP - 1) == 0
    assert out["fijo"] == [0.0, 0.0, 300.0, 300.0]
    assert out == lay.separate(rects, pinned={"fijo"})   # determinista


def test_recorrer_respeta_los_themes_editados_o_borrados_en_la_app(model):
    db = FakeDb()
    _run(db, model)
    themes = {t["name"]: t for t in db.data["diagram_themes"].values()}
    pri, sec = themes["Entidad Principal"], themes["Entidad Secundaria"]
    pri.update(name="Principal", color="#FFC000")        # renombrado y recoloreado en Data Standards
    sec["flgactive"] = False                              # borrado en Data Standards
    mig = _run(db, model)
    again = db.data["diagram_themes"]
    assert (again[pri["_id"]]["name"], again[pri["_id"]]["color"]) == ("Principal", "#FFC000")
    assert again[sec["_id"]]["flgactive"] is False       # no revive
    assert len(again) == 2
    tables = {t["physicalName"]: t for t in db.data["canonical_tables"].values()}
    assert tables["TAB_UNO"]["color"] == f"theme:{pri['_id']}"      # sigue al theme (no a un color fijo)
    assert mig.report["themes_deleted_in_app"] == [{"name": "Entidad Secundaria", "color": "#80A3FF"}]


def test_recorrer_no_revive_dibujos_ni_colores_quitados_en_la_app(model):
    db = FakeDb()
    _run(db, model)
    d1 = _canvas(db, "Diagrama Uno")
    d1["drawings"] = [x for x in d1["drawings"] if x["type"] != "line"]    # borró la línea
    d1["colors"] = {}                                                       # TAB_DOS vuelve al color de su tabla
    _run(db, model)
    again = _canvas(db, "Diagrama Uno")
    assert sorted(x["type"] for x in again["drawings"]) == ["rect", "text"]
    assert again["colors"] == {}


def test_dibujos_el_mas_grande_detras(model):
    db = FakeDb()
    _run(db, model)
    areas = [x["w"] * x["h"] for x in _canvas(db, "Diagrama Uno")["drawings"]]
    assert areas == sorted(areas, reverse=True)                  # el marco no tapa los textos


def test_un_texto_que_encierra_tablas_crece_como_un_marco(tmp_path):
    xml = FIXTURE.replace("<Anchor_Point>0 150</Anchor_Point><Fixed_Size_Point>200 30</Fixed_Size_Point>",
                          "<Anchor_Point>150 0</Anchor_Point><Fixed_Size_Point>400 120</Fixed_Size_Point>")
    assert xml != FIXTURE              # 120 de alto: le caben las tablas (Erwin ≈ 34 + 16.4 · filas)
    db = FakeDb()
    _run(db, _parse(tmp_path, xml, "texto_marco.xml"))
    d1 = _canvas(db, "Diagrama Uno")
    text = next(x for x in d1["drawings"] if x["type"] == "text")
    tables = {t["physicalName"]: t["_id"] for t in db.data["canonical_tables"].values()}
    for name, cols in (("TAB_UNO", 2), ("TAB_DOS", 1)):
        p = d1["layout"][tables[name]]
        assert text["x"] <= p["x"] and text["y"] <= p["y"]
        assert p["y"] + 32 + cols * 28 <= text["y"] + text["h"]


def test_vista_con_theme_que_pinta_distinto_tablas_y_vistas_queda_con_color_fijo(model):
    app = {"TH_PRI": "t-pri"}
    model.themes["TH_PRI"].view_fill = "#FFC000"         # Erwin pinta sus vistas de otro color
    assert col.object_color(model, "TH_PRI", "View", app) == "#FFC000"
    assert col.object_color(model, "TH_PRI", "Entity", app) == "theme:t-pri"
    model.themes["TH_PRI"].view_fill = model.themes["TH_PRI"].entity_fill
    assert col.object_color(model, "TH_PRI", "View", app) == "theme:t-pri"


def test_fusion_mide_lo_que_ya_estaba_y_no_lo_pisa(tmp_path):
    """Otro archivo de la familia aporta tablas nuevas al mismo canvas: las que
    ya estaban se miden con su tamaño real (no el típico) y nada se pisa."""
    cols = "".join(
        f'<Attribute id="AX{i}" name="columna con un nombre largo {i}"><AttributeProps>'
        f'<Name>columna con un nombre largo {i}</Name>'
        f'<User_Formatted_Physical_Name>COLUMNA_CON_UN_NOMBRE_MUY_LARGO_{i}</User_Formatted_Physical_Name>'
        f'<Physical_Data_Type>VARCHAR(100)</Physical_Data_Type><Physical_Order>{i + 3}</Physical_Order>'
        f'</AttributeProps></Attribute>' for i in range(40))
    a = FIXTURE.replace('</Attribute_Groups>\n   </Entity>\n   <Entity id="E2"',
                        cols + '</Attribute_Groups>\n   </Entity>\n   <Entity id="E2"', 1)
    assert a != FIXTURE
    db = FakeDb()
    m1 = Migrator(db, _parse(tmp_path, a, "a.xml"), "P", None, source_folder="Dom")
    m1.run()
    m2 = Migrator(db, _parse(tmp_path, _family_file(FIXTURE, "B", rename_tables=True), "b.xml"),
                  "P", None, source_folder="Dom")
    m2.run()
    canvas = _canvas(db, "Diagrama Uno")
    assert len(canvas["erwinLongIds"]) == 2                            # se fusionaron
    big = next(t["_id"] for t in db.data["canonical_tables"].values() if t["physicalName"] == "TAB_UNO")
    assert m2.box_size[big] == m1.box_size[big]                        # medida real, no la típica
    rects = {k: [p["x"], p["y"], *m2.box_size[k]] for k, p in canvas["layout"].items() if k in m2.box_size}
    assert len(rects) == len(canvas["layout"])
    assert lay.overlaps(rects, 0) == 0


def test_fusion_no_cambia_el_color_de_lo_que_ya_estaba(tmp_path, model):
    """Otro archivo dibuja la MISMA tabla en el mismo canvas con otro color: la
    caja que ya estaba conserva el suyo."""
    db = FakeDb()
    Migrator(db, model, "P", None, source_folder="Dom").run()
    b = _family_file(FIXTURE, "X").replace(
        f'<Entity_Fill_Color2 Derived="Y">{YELLOW}</Entity_Fill_Color2>', f"<Entity_Fill_Color2>255</Entity_Fill_Color2>", 1)
    assert "<Entity_Fill_Color2>255<" in b
    before = dict(_canvas(db, "Diagrama Uno")["colors"])
    Migrator(db, _parse(tmp_path, b, "x.xml"), "P", None, source_folder="Dom").run()
    assert _canvas(db, "Diagrama Uno")["colors"] == before


# ── segunda ronda de revisión del kit ───────────────────────────────────────


def test_familia_con_otro_color_del_mismo_theme_es_conflicto_y_sus_cajas_quedan_con_su_color(tmp_path, model):
    """Otro archivo del MISMO proyecto con «Entidad Principal» en otro color: no
    se pisa el theme ni se le prestan sus cajas — van con su color exacto."""
    db = FakeDb()
    Migrator(db, model, "P", None, source_folder="Dom").run()
    other = _family_file(FIXTURE, "Z", rename_tables=True).replace(
        f"<Entity_Fill_Color2>{YELLOW}</Entity_Fill_Color2>\n    <Entity_Fill_Style>4</Entity_Fill_Style>",
        "<Entity_Fill_Color2>12632256</Entity_Fill_Color2>\n    <Entity_Fill_Style>4</Entity_Fill_Style>", 1)
    assert "12632256" in other
    m2 = Migrator(db, _parse(tmp_path, other, "z.xml"), "P", None, source_folder="Dom")
    m2.run()
    pri = next(t for t in db.data["diagram_themes"].values() if t["name"] == "Entidad Principal")
    assert pri["color"] == "#FFFF80"                                     # no se pisa
    assert m2.report["theme_conflicts"] == [{"name": "Entidad Principal", "kept": "#FFFF80", "ignored": "#C0C0C0"}]
    tab = next(t for t in db.data["canonical_tables"].values() if t["physicalName"] == "TAB_UNO_Z")
    assert tab["color"] == "#C0C0C0"                                     # su color exacto, fijo
    # el mismo color en la familia sí se reusa (sin conflicto)
    m3 = Migrator(db, _parse(tmp_path, _family_file(FIXTURE, "Y", rename_tables=True), "y.xml"),
                  "P", None, source_folder="Dom")
    m3.run()
    assert m3.report["theme_conflicts"] == []
    tab_y = next(t for t in db.data["canonical_tables"].values() if t["physicalName"] == "TAB_UNO_Y")
    assert tab_y["color"] == f"theme:{pri['_id']}"


def test_theme_borrado_y_recreado_con_su_nombre_en_la_app_se_reusa(model):
    db = FakeDb()
    _run(db, model)
    sec = next(t for t in db.data["diagram_themes"].values() if t["name"] == "Entidad Secundaria")
    sec["flgactive"] = False
    db.data["diagram_themes"]["nuevo"] = {"_id": "nuevo", "projectId": sec["projectId"], "flgactive": True,
                                          "name": "Entidad Secundaria", "color": "#80A3FF"}
    mig = _run(db, model)
    assert mig.app_theme["TH_SEC"] == "nuevo"
    assert mig.report["themes_deleted_in_app"] == []


def test_tabla_reescrita_que_ya_existia_mide_tambien_las_columnas_que_conserva_la_bd(model):
    db = FakeDb()
    _run(db, model)
    tab = next(t for t in db.data["canonical_tables"].values() if t["physicalName"] == "TAB_DOS")
    for i in range(30):                          # columnas agregadas en la app (sin erwinLongId)
        db.data["canonical_columns"][f"app{i}"] = {"_id": f"app{i}", "tableId": tab["_id"], "flgactive": True,
                                                   "physicalName": f"COL_APP_{i}", "logicalName": f"col app {i}",
                                                   "dataType": "STRING"}
    mig = _run(db, model)
    assert mig.box_size[tab["_id"]][1] == lay.APP_HEADER_H + 31 * lay.APP_ROW_H


def test_fusion_del_mismo_diagrama_sin_bloques_nuevos_no_duplica_dibujos(tmp_path, model):
    db = FakeDb()
    Migrator(db, model, "P", None, source_folder="Dom").run()
    before = len(_canvas(db, "Diagrama Uno")["drawings"])
    m2 = Migrator(db, _parse(tmp_path, _family_file(FIXTURE, "X"), "x.xml"), "P", None, source_folder="Dom")
    m2.run()
    assert len(_canvas(db, "Diagrama Uno")["drawings"]) == before
    assert m2.stats["dibujos de Erwin no traídos (el canvas ya los tiene o ya los tuvo: manda la app)"] == 3


def test_un_titulo_angosto_que_cruza_una_tabla_no_crece_como_marco(tmp_path):
    xml = FIXTURE.replace("<Anchor_Point>0 150</Anchor_Point><Fixed_Size_Point>200 30</Fixed_Size_Point>",
                          "<Anchor_Point>0 0</Anchor_Point><Fixed_Size_Point>2000 30</Fixed_Size_Point>")
    assert xml != FIXTURE
    db = FakeDb()
    _run(db, _parse(tmp_path, xml, "titulo.xml"))
    text = next(x for x in _canvas(db, "Diagrama Uno")["drawings"] if x["type"] == "text")
    assert text["h"] == round(30 * lay.SCALE)                       # no creció a lo alto


def test_vista_sin_tabla_en_una_fuente_usa_la_primera_de_la_vista():
    rows = lay.view_rows([{"column": "COD", "outputAlias": "COD"}], {("t1", "COD"): "VARCHAR(20)"}, "t1")
    assert rows == [("COD", "VARCHAR(20)")]
    assert lay.erwin_height(2) == (34 + 2 * 16.4) * lay.SCALE
    assert lay.app_rows(lay.APP_HEADER_H + 2 * lay.APP_ROW_H) == 2


# ── tercera ronda de revisión del kit ───────────────────────────────────────


def test_theme_adoptado_por_nombre_y_luego_recoloreado_en_la_app_se_sigue_reusando(model):
    db = FakeDb()
    pid = Migrator(db, model, "Proyecto Estilos", None, source_folder="Origen").project_id
    db.data["diagram_themes"]["mio"] = {"_id": "mio", "projectId": pid, "flgactive": True,
                                        "name": "Entidad Principal", "color": "#FFFF80"}   # hecho en Data Standards
    mig = _run(db, model)
    assert mig.app_theme["TH_PRI"] == "mio"
    assert db.data["diagram_themes"]["mio"]["erwinColor"] == "#FFFF80"
    db.data["diagram_themes"]["mio"].update(color="#00B050", name="Principal")              # luego lo editan
    again = _run(db, model)
    assert again.app_theme["TH_PRI"] == "mio" and again.report["theme_conflicts"] == []
    tab = next(t for t in db.data["canonical_tables"].values() if t["physicalName"] == "TAB_UNO")
    assert tab["color"] == "theme:mio"
    assert not any(t["name"] == "Entidad Principal" for t in db.data["diagram_themes"].values())   # sin duplicado


def test_marco_alrededor_de_una_tabla_adoptada_mas_grande_crece(tmp_path):
    """Otro archivo de la familia (otros diagramas) adopta TAB_UNO, que en la BD
    tiene muchas más columnas que las que dibuja este XML: su marco la encierra."""
    cols = "".join(
        f'<Attribute id="AX{i}" name="col {i}"><AttributeProps><Name>col {i}</Name>'
        f'<User_Formatted_Physical_Name>COL_{i}</User_Formatted_Physical_Name>'
        f'<Physical_Data_Type>VARCHAR(10)</Physical_Data_Type><Physical_Order>{i + 3}</Physical_Order>'
        f'</AttributeProps></Attribute>' for i in range(40))
    a = FIXTURE.replace('</Attribute_Groups>\n   </Entity>\n   <Entity id="E2"',
                        cols + '</Attribute_Groups>\n   </Entity>\n   <Entity id="E2"', 1)
    db = FakeDb()
    Migrator(db, _parse(tmp_path, a, "a.xml"), "P", None, source_folder="Dom").run()
    b = _family_file(FIXTURE, "B").replace("Diagrama Uno", "Diagrama B").replace("Diagrama Dos", "Diagrama B2")
    m2 = Migrator(db, _parse(tmp_path, b, "b.xml"), "P", None, source_folder="Dom")
    m2.run()
    canvas = _canvas(db, "Diagrama B")
    frame = next(x for x in canvas["drawings"] if x["type"] == "rect")
    tab = next(t["_id"] for t in db.data["canonical_tables"].values() if t["physicalName"] == "TAB_UNO")
    p, (w, h) = canvas["layout"][tab], m2.box_size[tab]
    assert h == lay.APP_HEADER_H + 42 * lay.APP_ROW_H                       # lo que la app dibuja
    assert frame["y"] <= p["y"] and p["y"] + h <= frame["y"] + frame["h"]   # el marco la encierra


def test_fusion_con_bloques_nuevos_no_repite_dibujos_identicos(tmp_path, model):
    db = FakeDb()
    Migrator(db, model, "P", None, source_folder="Dom").run()
    before = len(_canvas(db, "Diagrama Uno")["drawings"])
    # la copia de la familia renombra UNA tabla (llega un bloque nuevo) y repite los dibujos
    other = _family_file(FIXTURE, "X").replace("TAB_DOS", "TAB_DOS_X")
    m2 = Migrator(db, _parse(tmp_path, other, "x.xml"), "P", None, source_folder="Dom")
    m2.run()
    drawings = _canvas(db, "Diagrama Uno")["drawings"]
    sigs = [(x["type"], (x.get("text") or "").strip(), x["w"], x["h"]) for x in drawings]
    assert len(sigs) == len(set(sigs)) and len(drawings) >= before
