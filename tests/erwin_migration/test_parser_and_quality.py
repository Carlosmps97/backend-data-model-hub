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
   <Domain id="D-COD" name="Codigo"><DomainProps><Name>Codigo</Name><Logical_Data_Type>VARCHAR(20)</Logical_Data_Type><Physical_Data_Type>VARCHAR(30)</Physical_Data_Type><Parent_Domain_Ref>D-ROOT</Parent_Domain_Ref><Definition>Dominio codigo</Definition></DomainProps></Domain>
  </Domain_Groups>
  <Entity_Groups>
   <Entity id="E1" name="Cliente Test">
    <EntityProps><Name>Cliente Test</Name><Physical_Name>%EntityName()</Physical_Name>
     <User_Formatted_Physical_Name>MD_CLIENTETEST</User_Formatted_Physical_Name>
     <Definition>Tabla de clientes</Definition>
     <Is_Logical_Only>false</Is_Logical_Only>
     <Attributes_Order_Ref_Array>
      <Attributes_Order_Ref index="0">A2</Attributes_Order_Ref>
      <Attributes_Order_Ref index="1">A1</Attributes_Order_Ref>
      <Attributes_Order_Ref index="2">A3</Attributes_Order_Ref>
     </Attributes_Order_Ref_Array>
     <Physical_Columns_Order_Ref_Array>
      <Physical_Columns_Order_Ref index="0">A1</Physical_Columns_Order_Ref>
      <Physical_Columns_Order_Ref index="1">A2</Physical_Columns_Order_Ref>
      <Physical_Columns_Order_Ref index="2">A3</Physical_Columns_Order_Ref>
     </Physical_Columns_Order_Ref_Array>
     <Columns_Order_Ref_Array>
      <Columns_Order_Ref index="0">A2</Columns_Order_Ref>
      <Columns_Order_Ref index="1">A3</Columns_Order_Ref>
      <Columns_Order_Ref index="2">A1</Columns_Order_Ref>
     </Columns_Order_Ref_Array>
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
      <Is_Logical_Only>true</Is_Logical_Only><Logical_Data_Type>VARCHAR(100)</Logical_Data_Type>
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
     <Is_Physical_Only>true</Is_Physical_Only>
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


def test_udp_por_faceta_logical_y_physical_separadas(model):
    defs = pol.udp_defs_by_view(model.udp_defs)
    keys = set(defs)
    assert {"table|physical|Criticidad", "table|logical|Criticidad", "canvas|physical|Dominio Modelo",
            "view|physical|Tipo de Vista"} <= keys
    assert not any("Subtype_Symbol" in k for k in keys)     # nivel no soportado
    vals = pol.resolve_udp_values(model.udp_values, defs)
    # Doc 69: cada faceta conserva SU valor (antes Physical pisaba Logical).
    assert vals[("E1", "table|physical|Criticidad")] == "Alta"
    assert vals[("E1", "table|logical|Criticidad")] == "Baja"
    assert vals[("M1", "canvas|physical|Dominio Modelo")] == "Riesgos"


def test_facetas_orden_logico_flags_y_tipo_fisico_dominio(model):
    e1 = model.entities["E1"]
    by_id = {a.id: a for a in e1.attributes}
    # `attributes` queda en el orden físico de la BD (A1, A2, A3: sólo para
    # deduplicar); el Column order de Erwin (A2, A3, A1) es la base del orden
    # único (doc 74).
    assert [a.id for a in e1.attributes] == ["A1", "A2", "A3"]
    assert (by_id["A2"].column_order, by_id["A3"].column_order, by_id["A1"].column_order) == (0, 1, 2)
    # Sin arrays de orden (E2) cae al Physical_Order.
    assert [a.column_order for a in model.entities["E2"].attributes] == [1, 2]
    assert by_id["A2"].logical_only is True and by_id["A1"].logical_only is False
    assert by_id["A2"].logical_type == "VARCHAR(100)"
    assert e1.logical_only is False and model.entities["E2"].physical_only is True
    assert model.domains["D-COD"].physical_type == "VARCHAR(30)"
    assert model.domains["D-COD"].data_type == "VARCHAR(20)"


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


_XML_SA_PUNTOS = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<erwin xmlns="http://www.erwin.com/dm" FileVersion="10.10" Format="erwin">
 <Model id="M1" name="Modelo Puntos"><ModelProps><Name>Modelo Puntos</Name></ModelProps>
  <Subject_Area_Groups>
   <Subject_Area id="SA1" name="1. Party"><Subject_AreaProps><Name>1. Party</Name>
    <Object_Order>0</Object_Order></Subject_AreaProps>
    <ER_Diagram id="DG1" name="Party y Cuenta"><ER_DiagramProps><Name>Party y Cuenta</Name>
     <Owner_Path>Modelo Puntos.1. Party</Owner_Path></ER_DiagramProps></ER_Diagram>
   </Subject_Area>
   <Subject_Area id="SA2" name="&lt;Vista Logica Completa&gt;"><Subject_AreaProps>
    <Name>&lt;Vista Logica Completa&gt;</Name><Object_Order>1</Object_Order></Subject_AreaProps>
    <ER_Diagram id="DG2" name="ER_Diagram_210"><ER_DiagramProps><Name>ER_Diagram_210</Name>
     <Owner_Path>Modelo Puntos.&lt;Vista Logica Completa&gt;</Owner_Path></ER_DiagramProps></ER_Diagram>
   </Subject_Area>
  </Subject_Area_Groups>
 </Model>
</erwin>
""")


def test_diagrama_resuelve_sa_con_puntos_en_el_nombre(tmp_path):
    """Doc 54 §8: las SAs numeradas ("1. Party") rompían el split del
    Owner_Path y los canvases caían sin folder (UDV: 188/189 sueltos)."""
    f = tmp_path / "puntos.xml"
    f.write_text(_XML_SA_PUNTOS, encoding="utf-8")
    m = ep.parse(str(f))
    assert {d.name: d.subject_area for d in m.diagrams} == {
        "Party y Cuenta": "1. Party",
        "ER_Diagram_210": "<Vista Logica Completa>",
    }


def test_column_order_cae_al_attribute_order_sin_columns_array(tmp_path):
    """Doc 74: sin `Columns_Order_Ref_Array` el Column order nace del Attribute
    order (así lo inicializa Erwin); el Physical_Order sólo es el último fallback."""
    xml = textwrap.dedent(f"""\
    <?xml version="1.0" encoding="UTF-8"?>
    <erwin xmlns="http://www.erwin.com/dm" FileVersion="10.10" Format="erwin">
     <Model {NS} id="M1" name="Mini"><ModelProps><Name>Mini</Name></ModelProps>
      <Entity_Groups>
       <Entity id="E1" name="Uno">
        <EntityProps><Name>Uno</Name><Physical_Name>UNO</Physical_Name><User_Formatted_Physical_Name>UNO</User_Formatted_Physical_Name>
         <Attributes_Order_Ref_Array>
          <Attributes_Order_Ref index="0">A2</Attributes_Order_Ref>
          <Attributes_Order_Ref index="1">A1</Attributes_Order_Ref>
         </Attributes_Order_Ref_Array>
        </EntityProps>
        <Attribute_Groups>
         <Attribute id="A1" name="a"><AttributeProps><Name>a</Name><User_Formatted_Physical_Name>A</User_Formatted_Physical_Name>
          <Physical_Data_Type>STRING</Physical_Data_Type><Physical_Order>1</Physical_Order></AttributeProps></Attribute>
         <Attribute id="A2" name="b"><AttributeProps><Name>b</Name><User_Formatted_Physical_Name>B</User_Formatted_Physical_Name>
          <Physical_Data_Type>STRING</Physical_Data_Type><Physical_Order>2</Physical_Order></AttributeProps></Attribute>
        </Attribute_Groups>
       </Entity>
      </Entity_Groups>
     </Model>
    </erwin>
    """)
    p = tmp_path / "mini.xml"
    p.write_text(xml, encoding="utf-8")
    m = ep.parse(str(p))
    by_id = {a.id: a for a in m.entities["E1"].attributes}
    assert (by_id["A2"].column_order, by_id["A1"].column_order) == (0, 1)


def test_dominio_atributo_estandar_captura_attribute_definition(tmp_path):
    """Doc 79: el parser captura `Attribute_Definition` (la marca del dominio
    "atributo estándar"); un dominio genérico de tipo lo deja vacío."""
    xml = textwrap.dedent(f"""\
    <?xml version="1.0" encoding="UTF-8"?>
    <erwin xmlns="http://www.erwin.com/dm" FileVersion="10.10" Format="erwin">
     <Model {NS} id="M" name="M">
      <Domain_Groups>
       <Domain id="D-GEN" name="Codigo"><DomainProps><Name>Codigo</Name><Logical_Data_Type>VARCHAR(20)</Logical_Data_Type><Definition>Dominio codigo</Definition></DomainProps></Domain>
       <Domain id="D-STD" name="FecRutina"><DomainProps><Name>FecRutina</Name><Logical_Data_Type>DATE</Logical_Data_Type><Attribute_Definition>Fecha de la rutina.</Attribute_Definition></DomainProps></Domain>
      </Domain_Groups>
     </Model>
    </erwin>
    """)
    p = tmp_path / "d.xml"
    p.write_text(xml, encoding="utf-8")
    m = ep.parse(str(p))
    assert m.domains["D-STD"].attribute_definition == "Fecha de la rutina."
    assert m.domains["D-GEN"].attribute_definition == ""


def test_dominio_captura_fisico_comment_y_udp_derivados_doc85(tmp_path):
    """Doc 85: el <Domain> trae físico resuelto, Comment y UDP de atributo en
    ambas facetas; los `Derived="Y"` del DOMINIO son valores heredados de la
    cadena de padres materializados por Erwin ⇒ se capturan. En un atributo,
    un derivado sigue siendo herencia del dominio ⇒ se sigue descartando."""
    xml = textwrap.dedent(f"""\
    <?xml version="1.0" encoding="UTF-8"?>
    <erwin xmlns="http://www.erwin.com/dm" FileVersion="10.10" Format="erwin">
     <Model {NS} id="M" name="M">
      <Domain_Groups>
       <Domain id="D-CC" name="Codigo Clave"><DomainProps><Name>Codigo Clave</Name><Definition>Clave.</Definition><Comment>Clave fisica.</Comment>
        <Physical_Name Derived="Y">%DomainName</Physical_Name><User_Formatted_Physical_Name ReadOnly="Y" Derived="Y">CodigoClave</User_Formatted_Physical_Name>
        <Logical_Data_Type>VARCHAR(20)</Logical_Data_Type><Physical_Data_Type>VARCHAR(30)</Physical_Data_Type>
        <UDP_Instance_Groups>
         <UDP_Instance name="Attribute.Logical.Atributo Cross" id="U-AC" Derived="Y"> No</UDP_Instance>
         <UDP_Instance name="Attribute.Physical.Campo Cross" id="U-CC"> No</UDP_Instance>
        </UDP_Instance_Groups>
       </DomainProps></Domain>
       <Domain id="D-RAW" name="Raw"><DomainProps><Name>Raw</Name><Physical_Name>RAW_F</Physical_Name><Logical_Data_Type>DATE</Logical_Data_Type></DomainProps></Domain>
      </Domain_Groups>
      <Entity_Groups>
       <Entity id="E1" name="Uno"><EntityProps><Name>Uno</Name><User_Formatted_Physical_Name>UNO</User_Formatted_Physical_Name></EntityProps>
        <Attribute_Groups>
         <Attribute id="A1" name="a"><AttributeProps><Name>a</Name><User_Formatted_Physical_Name>A</User_Formatted_Physical_Name>
          <Physical_Data_Type>STRING</Physical_Data_Type><Physical_Order>1</Physical_Order>
          <UDP_Instance_Groups>
           <UDP_Instance name="Attribute.Logical.Atributo Cross" id="U-AC" Derived="Y"> No</UDP_Instance>
           <UDP_Instance name="Attribute.Physical.Campo Cross" id="U-CC">Si</UDP_Instance>
          </UDP_Instance_Groups>
         </AttributeProps></Attribute>
        </Attribute_Groups>
       </Entity>
      </Entity_Groups>
     </Model>
    </erwin>
    """)
    p = tmp_path / "d85.xml"
    p.write_text(xml, encoding="utf-8")
    m = ep.parse(str(p))
    d = m.domains["D-CC"]
    assert (d.physical_name, d.comment, d.definition) == ("CodigoClave", "Clave fisica.", "Clave.")
    assert m.domains["D-RAW"].physical_name == "RAW_F"          # sin macro: vale el Physical_Name crudo
    vals = {(o, t, u): v for o, t, u, v in m.udp_values}
    assert vals[("D-CC", "Domain", "U-AC")] == "No" and vals[("D-CC", "Domain", "U-CC")] == "No"
    assert ("A1", "Attribute", "U-AC") not in vals and vals[("A1", "Attribute", "U-CC")] == "Si"
