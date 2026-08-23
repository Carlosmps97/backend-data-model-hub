"""Tests del paquete scripts/erwin_migration (parser + políticas + quality).

Fixture: mini-XML sintético con la estructura real de Erwin (namespace,
Props anidados, *_Groups) que ejercita cada regla: columna duplicada, macro
sin resolver, tabla sin schema, vista con derivación y columna passthrough,
relación con par FK, dominio custom/builtin, glosario con separador \\#x1F,
UDP Logical/Physical colapsables, diagrama con shapes y anotación.
"""
from __future__ import annotations

import textwrap

import pytest

from scripts.erwin_migration import erwin_parser as ep
from scripts.erwin_migration import policies as pol
from scripts.erwin_migration.quality import analyze, glossary_cross_conflicts, summarize

NS = 'xmlns="http://www.erwin.com/dm/data"'

FIXTURE = textwrap.dedent(f"""\
<?xml version="1.0" encoding="UTF-8"?>
<erwin xmlns="http://www.erwin.com/dm" FileVersion="10.10" Format="erwin">
 <Model {NS} id="M1" name="Modelo Test">
  <ModelProps><Name>Modelo Test</Name>
   <UDP_Instance_Groups>
    <UDP_Instance name="Model.Physical.Dominio Modelo" id="UDM1">Riesgos</UDP_Instance>
   </UDP_Instance_Groups>
  </ModelProps>
  <Domain_Groups>
   <Domain id="D-ROOT" name="&lt;root&gt;"><DomainProps><Built_In_Id>6</Built_In_Id><Name>&lt;root&gt;</Name></DomainProps></Domain>
   <Domain id="D-COD" name="Codigo"><DomainProps><Name>Codigo</Name><Logical_Data_Type>VARCHAR(20)</Logical_Data_Type><Parent_Domain_Ref>D-ROOT</Parent_Domain_Ref><Definition>Dominio codigo</Definition></DomainProps></Domain>
  </Domain_Groups>
  <Entity_Groups>
   <Entity id="E1" name="Cliente Test">
    <EntityProps><Name>Cliente Test</Name><Physical_Name>%EntityName()</Physical_Name>
     <User_Formatted_Physical_Name>MD_CLIENTETEST</User_Formatted_Physical_Name>
     <Definition>Tabla de clientes</Definition>
     <UDP_Instance_Groups>
      <UDP_Instance name="Entity.Physical.Criticidad" id="UDE1">Alta</UDP_Instance>
      <UDP_Instance name="Entity.Logical.Criticidad" id="UDE2">Baja</UDP_Instance>
      <UDP_Instance name="Entity.Physical.Sin Valor" id="UDE3" Derived="Y">x</UDP_Instance>
     </UDP_Instance_Groups>
    </EntityProps>
    <Attribute_Groups>
     <Attribute id="A1" name="codigo cliente"><AttributeProps><Name>codigo cliente</Name>
      <Physical_Name>CODCLI</Physical_Name><User_Formatted_Physical_Name>CODCLI</User_Formatted_Physical_Name>
      <Physical_Data_Type>VARCHAR(20)</Physical_Data_Type><Null_Option_Type>1</Null_Option_Type>
      <Physical_Order>1</Physical_Order><Definition>Codigo del cliente</Definition>
      <Parent_Domain_Ref>D-COD</Parent_Domain_Ref></AttributeProps></Attribute>
     <Attribute id="A2" name="nombre"><AttributeProps><Name>nombre</Name>
      <User_Formatted_Physical_Name>NOMBRE</User_Formatted_Physical_Name>
      <Physical_Data_Type>VARCHAR(120)</Physical_Data_Type><Null_Option_Type>0</Null_Option_Type>
      <Physical_Order>2</Physical_Order></AttributeProps></Attribute>
     <Attribute id="A3" name="nombre dup"><AttributeProps><Name>nombre dup</Name>
      <User_Formatted_Physical_Name>NOMBRE</User_Formatted_Physical_Name>
      <Physical_Data_Type>VARCHAR(120)</Physical_Data_Type><Null_Option_Type>0</Null_Option_Type>
      <Physical_Order>3</Physical_Order></AttributeProps></Attribute>
    </Attribute_Groups>
    <Key_Group_Groups>
     <Key_Group id="K1" name="XPK1"><Key_GroupProps><Key_Group_Type>PK</Key_Group_Type>
      <Key_Group_Members_Order_Ref_Array><Key_Group_Members_Order_Ref index="0">A1</Key_Group_Members_Order_Ref></Key_Group_Members_Order_Ref_Array>
     </Key_GroupProps></Key_Group>
     <Key_Group id="K2" name="IDX1"><Key_GroupProps><Key_Group_Type>IF1</Key_Group_Type></Key_GroupProps></Key_Group>
    </Key_Group_Groups>
   </Entity>
   <Entity id="E2" name="Cuenta Test">
    <EntityProps><Name>Cuenta Test</Name><Physical_Name>MD_CUENTATEST</Physical_Name>
     <User_Formatted_Physical_Name>MD_CUENTATEST</User_Formatted_Physical_Name></EntityProps>
    <Attribute_Groups>
     <Attribute id="B1" name="codigo cuenta"><AttributeProps><Name>codigo cuenta</Name>
      <User_Formatted_Physical_Name>CODCTA</User_Formatted_Physical_Name>
      <Physical_Data_Type>VARCHAR(20)</Physical_Data_Type><Null_Option_Type>1</Null_Option_Type>
      <Physical_Order>1</Physical_Order></AttributeProps></Attribute>
     <Attribute id="B2" name="codigo cliente"><AttributeProps><Name>codigo cliente</Name>
      <User_Formatted_Physical_Name>CODCLI</User_Formatted_Physical_Name>
      <Physical_Data_Type>VARCHAR(20)</Physical_Data_Type><Null_Option_Type>0</Null_Option_Type>
      <Physical_Order>2</Physical_Order>
      <Parent_Attribute_Ref>A1</Parent_Attribute_Ref>
      <Parent_Relationship_Ref>R1</Parent_Relationship_Ref></AttributeProps></Attribute>
    </Attribute_Groups>
   </Entity>
  </Entity_Groups>
  <View_Groups>
   <View id="V1" name="MD_CLIENTETEST_VU"><ViewProps><Name>MD_CLIENTETEST_VU</Name></ViewProps>
    <Attribute_Groups>
     <Attribute id="VA1" name="CODCLI"><AttributeProps><Name>CODCLI</Name>
      <User_Formatted_Physical_Name>CODCLI</User_Formatted_Physical_Name>
      <Physical_Data_Type>STRING</Physical_Data_Type><Null_Option_Type>0</Null_Option_Type>
      <Physical_Order>1</Physical_Order>
      <Parent_Attribute_Ref>A1</Parent_Attribute_Ref>
      <Parent_Relationship_Ref>R2</Parent_Relationship_Ref></AttributeProps></Attribute>
     <Attribute id="VA2" name="HUERFANA"><AttributeProps><Name>HUERFANA</Name>
      <User_Formatted_Physical_Name>HUERFANA</User_Formatted_Physical_Name>
      <Physical_Data_Type>STRING</Physical_Data_Type><Null_Option_Type>0</Null_Option_Type>
      <Physical_Order>2</Physical_Order></AttributeProps></Attribute>
    </Attribute_Groups>
   </View>
   <View id="V2" name="VW_SIN_FUENTE"><ViewProps><Name>VW_SIN_FUENTE</Name></ViewProps></View>
  </View_Groups>
  <Relationship_Groups>
   <Relationship id="R1" name="R/1"><RelationshipProps><Type>7</Type><Cardinality>-3</Cardinality>
    <Null_Option_Type>100</Null_Option_Type>
    <Parent_Entity_Ref>E1</Parent_Entity_Ref><Child_Entity_Ref>E2</Child_Entity_Ref></RelationshipProps></Relationship>
   <Relationship id="R2" name="R/2"><RelationshipProps><Type>16</Type><Cardinality>-3</Cardinality>
    <Parent_Entity_Ref>E1</Parent_Entity_Ref><Child_Entity_Ref>V1</Child_Entity_Ref></RelationshipProps></Relationship>
  </Relationship_Groups>
  <Hive_Database_Groups>
   <Hive_Database id="HD1" name="bcp_test"><Hive_DatabaseProps><Name>bcp_test</Name>
    <Dependent_Objects_Ref_Array><Dependent_Objects_Ref index="0">E1</Dependent_Objects_Ref>
     <Dependent_Objects_Ref index="1">V1</Dependent_Objects_Ref></Dependent_Objects_Ref_Array>
   </Hive_DatabaseProps></Hive_Database>
  </Hive_Database_Groups>
  <Subject_Area_Groups>
   <Subject_Area id="SA1" name="AreaUno"><Subject_AreaProps><Name>AreaUno</Name>
    <Definition>Subject de prueba</Definition><Object_Order>0</Object_Order></Subject_AreaProps>
    <ER_Diagram id="DG1" name="DiagUno"><ER_DiagramProps><Name>DiagUno</Name>
     <Owner_Path>Modelo Test.AreaUno</Owner_Path></ER_DiagramProps>
     <ER_Model_Shape_Groups>
      <ER_Model_Shape id="S1" name="Cliente Test"><ER_Model_ShapeProps>
       <Model_Object_Ref>E1</Model_Object_Ref><Anchor_Point>-10 20</Anchor_Point>
       <Owner_Path>Modelo Test.AreaUno.DiagUno</Owner_Path></ER_Model_ShapeProps></ER_Model_Shape>
      <ER_Model_Shape id="S2" name="Vista"><ER_Model_ShapeProps>
       <Model_Object_Ref>V1</Model_Object_Ref>
       <Owner_Path>Modelo Test.AreaUno.DiagUno</Owner_Path></ER_Model_ShapeProps></ER_Model_Shape>
     </ER_Model_Shape_Groups>
    </ER_Diagram>
   </Subject_Area>
  </Subject_Area_Groups>
  <Annotation_Groups><Annotation id="AN1" name="Nota"><AnnotationProps><Name>Nota</Name></AnnotationProps></Annotation></Annotation_Groups>
 </Model>
 <UDP_Definition_Groups xmlns="http://www.erwin.com/dm/metadata">
  <Property_Type id="UDM1" name="Model.Physical.Dominio Modelo"><Property_TypeProps><tag_Udp_Data_Type>6</tag_Udp_Data_Type><tag_Udp_Default_Value>No Definido</tag_Udp_Default_Value></Property_TypeProps></Property_Type>
  <Property_Type id="UDE1" name="Entity.Physical.Criticidad"><Property_TypeProps><tag_Udp_Data_Type>6</tag_Udp_Data_Type></Property_TypeProps></Property_Type>
  <Property_Type id="UDE2" name="Entity.Logical.Criticidad"><Property_TypeProps><tag_Udp_Data_Type>6</tag_Udp_Data_Type></Property_TypeProps></Property_Type>
  <Property_Type id="UDE3" name="Entity.Physical.Sin Valor"><Property_TypeProps><tag_Udp_Data_Type>2</tag_Udp_Data_Type></Property_TypeProps></Property_Type>
  <Property_Type id="UDV1" name="View.Physical.Tipo de Vista"><Property_TypeProps><tag_Udp_Data_Type>6</tag_Udp_Data_Type></Property_TypeProps></Property_Type>
  <Property_Type id="UDS1" name="Subtype_Symbol.Physical.X"><Property_TypeProps/></Property_Type>
 </UDP_Definition_Groups>
 <EMX_Glossary xmlns="http://www.erwin.com/dm/data">
  <Glossary_Word_List_Array>
   <Glossary_Word_List HandleNonPrintableChar="Y" index="0">Codigo\\#x1FCOD\\#x1F\\#x1F</Glossary_Word_List>
   <Glossary_Word_List HandleNonPrintableChar="Y" index="1">Cliente\\#x1FCLI\\#x1F\\#x1F</Glossary_Word_List>
   <Glossary_Word_List HandleNonPrintableChar="Y" index="2">Codigo\\#x1FCOD2\\#x1F\\#x1F</Glossary_Word_List>
  </Glossary_Word_List_Array>
 </EMX_Glossary>
</erwin>
""")


@pytest.fixture(scope="module")
def model(tmp_path_factory) -> ep.ErwinModel:
    p = tmp_path_factory.mktemp("erwin") / "mini.xml"
    p.write_text(FIXTURE, encoding="utf-8")
    return ep.parse(str(p))


# ── parser ──────────────────────────────────────────────────────────────
def test_entities_y_atributos(model):
    assert set(model.entities) == {"E1", "E2"}
    e1 = model.entities["E1"]
    assert e1.physical == "MD_CLIENTETEST" and e1.physical_was_macro
    assert e1.definition == "Tabla de clientes"
    assert [a.physical for a in e1.attributes] == ["CODCLI", "NOMBRE", "NOMBRE"]
    a1 = e1.attributes[0]
    assert a1.domain_ref == "D-COD" and a1.nullable is False
    assert e1.pk_attr_ids == {"A1"} and e1.index_key_groups == 1


def test_vistas_con_columnas_y_derivacion(model):
    v1 = model.views["V1"]
    assert [a.physical for a in v1.attributes] == ["CODCLI", "HUERFANA"]
    assert v1.attributes[0].parent_attr_ref == "A1"
    rels = model.view_source_rels()
    assert [r.parent_ref for r in rels["V1"]] == ["E1"]
    assert "V2" not in rels


def test_relaciones_y_pares_fk(model):
    assert model.relationships["R1"].rel_type == ep.REL_NON_IDENTIFYING
    assert model.fk_pairs()["R1"] == [("A1", "B2")]
    # Null_Option_Type de la relación (100 = nulls allowed → padre 0..1).
    assert model.relationships["R1"].null_option == ep.REL_NULLS_ALLOWED


def test_parent_cardinality_desde_null_option():
    assert pol.map_parent_cardinality("100") == "zero-one"
    assert pol.map_parent_cardinality("101") == "one"
    assert pol.map_parent_cardinality("") == "one"       # sin dato → conservador


def test_particion_correlativo_y_marks():
    # Parseo del valor UDP (convención DDV): PART_nn; el resto NO es partición.
    assert pol.partition_correlative("PART_01") == 1
    assert pol.partition_correlative("part-2") == 2
    assert pol.partition_correlative("PART10") == 10
    assert pol.partition_correlative("No Definido") is None
    assert pol.partition_correlative("") is None
    assert pol.partition_correlative(None) is None
    # Congruente: correlativo asciende en orden físico → se marca todo.
    assert pol.partition_marks([("a", 1), ("b", 2)]) == ({"a", "b"}, None)
    # Huecos 1..n tolerados (el orden sigue bien definido).
    assert pol.partition_marks([("a", 1), ("b", 3)]) == ({"a", "b"}, None)
    # v2 (doc 32b R6): duplicado o desorden → se marca IGUAL (el orden
    # efectivo es el físico) y el motivo va al reporte como reasignación.
    ids, motivo = pol.partition_marks([("a", 1), ("b", 1)])
    assert ids == {"a", "b"} and "duplicado" in motivo and "reasignado" in motivo
    ids, motivo = pol.partition_marks([("a", 2), ("b", 1)])
    assert ids == {"a", "b"} and "orden" in motivo and "reasignado" in motivo
    assert pol.partition_marks([]) == (set(), None)


def test_schema_dominios_glosario_udp(model):
    schema = model.owner_schema()
    assert schema == {"E1": "bcp_test", "V1": "bcp_test"}   # E2 y V2 sin schema
    assert model.domains["D-ROOT"].builtin and not model.domains["D-COD"].builtin
    assert ("Codigo", "COD") == model.glossary[0][:2] and len(model.glossary) == 3
    assert model.annotations == 1 and model.subtype_udp_defs == 1
    # UDP: solo valores NO derivados
    assert ("E1", "Entity", "UDE1", "Alta") in model.udp_values
    assert all(d != "UDE3" for _o, _t, d, _v in model.udp_values)


def test_diagrama_shapes(model):
    d = model.diagrams[0]
    assert d.subject_area == "AreaUno"
    assert [ref for ref, _a in d.shapes] == ["E1", "V1"]
    assert d.shapes[0][1] == "-10 20" and d.shapes[1][1] is None


# ── políticas ───────────────────────────────────────────────────────────
def test_dedupe_columnas_conserva_primera(model):
    keep, dropped = pol.dedupe_columns(model.entities["E1"].attributes)
    assert [a.physical for a in keep] == ["CODCLI", "NOMBRE"]
    assert [a.id for a in dropped] == ["A3"]


def test_colapso_udp_logical_physical_y_valores(model):
    collapsed = pol.collapse_udp_defs(model.udp_defs)
    keys = set(collapsed)
    assert "table|Criticidad" in keys and "canvas|Dominio Modelo" in keys
    assert "View.Physical.Tipo de Vista" not in str(keys)  # nivel no soportado
    vals = pol.resolve_udp_values(model.udp_values, collapsed)
    # Physical (Alta) pisa Logical (Baja)
    assert vals[("E1", "table|Criticidad")] == "Alta"
    assert vals[("M1", "canvas|Dominio Modelo")] == "Riesgos"


def test_ids_deterministas_y_schema_default():
    assert pol.platform_id("X") == pol.platform_id("X") != pol.platform_id("Y")
    assert pol.schema_or_default(None) == "No_Definido"
    assert pol.map_cardinality("-3") == "zero-many"
    assert pol.map_cardinality("7") == "many"


# ── quality gate ────────────────────────────────────────────────────────
def test_quality_detecta_las_incongruencias(model):
    by_code = {x["code"]: x for x in analyze(model)}
    assert by_code["W-NO-SCHEMA-TABLE"]["count"] == 1        # E2
    assert by_code["W-MACRO-PHYSNAME"]["count"] == 1         # E1
    assert by_code["W-DUP-COLUMN"]["count"] == 1             # NOMBRE ×2
    # política 2026-07-24 (doc 32b R7): sin fuente = WARN, se descarta
    assert by_code["W-VIEW-NO-SOURCE"]["items"] == ["VW_SIN_FUENTE"]
    assert by_code["W-VIEW-NO-SOURCE"]["severity"] == "WARN"
    assert by_code["W-VIEWCOL-NO-ORIGIN"]["count"] == 1      # HUERFANA
    assert by_code["E-GLOSSARY-DUP-TERM"]["count"] == 1      # Codigo ×2
    assert by_code["I-ANNOTATIONS-DISCARDED"]["count"] == 1
    assert by_code["I-INDEXES-SKIPPED"]["count"] == 1
    assert "W-DUP-TABLE" not in by_code and "E-DUP-TABLE" not in by_code
    s = summarize(model)
    assert s["tablas"] == 2 and s["vistas"] == 2 and s["diagramas"] == 1
    assert s["columnas_tabla"] == 5 and s["columnas_vista"] == 2


# ── glosario ENTRE archivos (full outer join, política 2026-08-22) ──────
def test_glosario_cross_file_solo_reporta_abreviaturas_en_conflicto():
    files = [
        ("a.xml", [("Monto", "MTO"), ("Codigo", "COD"), ("Solo A", "SA"),
                   ("Monto", "OTRA")]),          # dup interno: manda la 1ª
        ("b.xml", [("Monto", "MTOS"), ("Codigo", "cod"), ("Solo B", "SB")]),
    ]
    out = glossary_cross_conflicts(files)
    # unión sin conflicto (Solo A/Solo B) no reporta; case-insensitive
    # (COD ≡ cod) tampoco; "Monto" MTO vs MTOS SÍ.
    assert [c["term"] for c in out] == ["Monto"]
    assert out[0]["byFile"] == {"a.xml": "MTO", "b.xml": "MTOS"}


def test_glosario_cross_file_sin_archivos_compartidos_es_limpio():
    assert glossary_cross_conflicts([("a.xml", [("Monto", "MTO")])]) == []
    assert glossary_cross_conflicts([
        ("a.xml", [("Monto", "MTO")]),
        ("b.xml", [("Monto", "MTO"), ("Codigo", "")]),   # vacía no choca
        ("c.xml", [("Codigo", "COD")]),
    ]) == []
