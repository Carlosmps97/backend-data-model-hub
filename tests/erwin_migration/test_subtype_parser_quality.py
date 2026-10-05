"""Subcategorías en el kit Erwin (doc 53): parser del Subtype_Symbol, entrada
de las relaciones Type 9 al gate de calidad y findings nuevos de duplicados.

Fixture propio (no toca el mini-XML general): Party con subtipo Individuo
agrupados por un símbolo, una relación Type 9 huérfana (sin símbolo), un hijo
en DOS grupos y un homónimo físico en otro esquema.
"""
from __future__ import annotations

import textwrap

import pytest

from scripts.erwin_migration import erwin_parser as ep
from scripts.erwin_migration.quality import analyze, summarize

FIXTURE = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<erwin xmlns="http://www.erwin.com/dm" FileVersion="10.10" Format="erwin">
 <Model xmlns="http://www.erwin.com/dm/data" id="M1" name="Modelo Subtipos">
  <ModelProps><Name>Modelo Subtipos</Name></ModelProps>
  <Entity_Groups>
   <Entity id="E1" name="Party">
    <EntityProps><Name>Party</Name><Physical_Name>PARTY</Physical_Name>
     <User_Formatted_Physical_Name>PARTY</User_Formatted_Physical_Name></EntityProps>
    <Attribute_Groups>
     <Attribute id="A1" name="codigo party"><AttributeProps><Name>codigo party</Name>
      <User_Formatted_Physical_Name>CODPARTY</User_Formatted_Physical_Name>
      <Physical_Data_Type>VARCHAR(20)</Physical_Data_Type><Null_Option_Type>1</Null_Option_Type>
      <Physical_Order>1</Physical_Order></AttributeProps></Attribute>
    </Attribute_Groups>
    <Key_Group_Groups>
     <Key_Group id="K1" name="XPK1"><Key_GroupProps><Key_Group_Type>PK</Key_Group_Type>
      <Key_Group_Members_Order_Ref_Array><Key_Group_Members_Order_Ref index="0">A1</Key_Group_Members_Order_Ref></Key_Group_Members_Order_Ref_Array>
     </Key_GroupProps></Key_Group>
    </Key_Group_Groups>
   </Entity>
   <Entity id="E2" name="Individuo">
    <EntityProps><Name>Individuo</Name><Physical_Name>INDIVIDUO</Physical_Name>
     <User_Formatted_Physical_Name>INDIVIDUO</User_Formatted_Physical_Name></EntityProps>
    <Attribute_Groups>
     <Attribute id="B1" name="codigo party"><AttributeProps><Name>codigo party</Name>
      <User_Formatted_Physical_Name>CODPARTY</User_Formatted_Physical_Name>
      <Physical_Data_Type>VARCHAR(20)</Physical_Data_Type><Null_Option_Type>1</Null_Option_Type>
      <Physical_Order>1</Physical_Order>
      <Parent_Attribute_Ref>A1</Parent_Attribute_Ref>
      <Parent_Relationship_Ref>R9</Parent_Relationship_Ref></AttributeProps></Attribute>
     <Attribute id="B2" name="codigo party bis"><AttributeProps><Name>codigo party bis</Name>
      <User_Formatted_Physical_Name>CODPARTYBIS</User_Formatted_Physical_Name>
      <Physical_Data_Type>VARCHAR(20)</Physical_Data_Type><Null_Option_Type>1</Null_Option_Type>
      <Physical_Order>2</Physical_Order>
      <Parent_Attribute_Ref>C1</Parent_Attribute_Ref>
      <Parent_Relationship_Ref>R9E</Parent_Relationship_Ref></AttributeProps></Attribute>
    </Attribute_Groups>
    <Key_Group_Groups>
     <Key_Group id="K2" name="XPK2"><Key_GroupProps><Key_Group_Type>PK</Key_Group_Type>
      <Key_Group_Members_Order_Ref_Array><Key_Group_Members_Order_Ref index="0">B1</Key_Group_Members_Order_Ref></Key_Group_Members_Order_Ref_Array>
     </Key_GroupProps></Key_Group>
    </Key_Group_Groups>
   </Entity>
   <Entity id="E3" name="Party">
    <EntityProps><Name>Party</Name><Physical_Name>PARTY</Physical_Name>
     <User_Formatted_Physical_Name>PARTY</User_Formatted_Physical_Name></EntityProps>
    <Attribute_Groups>
     <Attribute id="C1" name="codigo party"><AttributeProps><Name>codigo party</Name>
      <User_Formatted_Physical_Name>CODPARTY</User_Formatted_Physical_Name>
      <Physical_Data_Type>VARCHAR(20)</Physical_Data_Type><Null_Option_Type>1</Null_Option_Type>
      <Physical_Order>1</Physical_Order>
      <Parent_Attribute_Ref>A1</Parent_Attribute_Ref>
      <Parent_Relationship_Ref>R9C</Parent_Relationship_Ref></AttributeProps></Attribute>
    </Attribute_Groups>
   </Entity>
  </Entity_Groups>
  <Relationship_Groups>
   <Relationship id="R9" name="R/9"><RelationshipProps><Type>9</Type><Cardinality>-1</Cardinality>
    <Null_Option_Type>101</Null_Option_Type>
    <Parent_To_Child_Verb_Phrase>Party es un Individuo</Parent_To_Child_Verb_Phrase>
    <Parent_Entity_Ref>E1</Parent_Entity_Ref><Child_Entity_Ref>E2</Child_Entity_Ref></RelationshipProps></Relationship>
   <Relationship id="R9C" name="R/9C"><RelationshipProps><Type>9</Type><Cardinality>-1</Cardinality>
    <Null_Option_Type>101</Null_Option_Type>
    <Parent_Entity_Ref>E1</Parent_Entity_Ref><Child_Entity_Ref>E3</Child_Entity_Ref></RelationshipProps></Relationship>
   <Relationship id="R9E" name="R/9E"><RelationshipProps><Type>9</Type><Cardinality>-1</Cardinality>
    <Null_Option_Type>101</Null_Option_Type>
    <Parent_Entity_Ref>E3</Parent_Entity_Ref><Child_Entity_Ref>E2</Child_Entity_Ref></RelationshipProps></Relationship>
  </Relationship_Groups>
  <Subtype_Symbol_Groups>
   <Subtype_Symbol id="SY1" name="Subtype_Symbol_Party"><Subtype_SymbolProps>
    <Name>Subtype_Symbol_Party</Name><Type>19</Type><Hide_In_Physical>true</Hide_In_Physical>
    <Relationships_Ref_Array><Relationships_Ref index="0">R9</Relationships_Ref></Relationships_Ref_Array>
   </Subtype_SymbolProps></Subtype_Symbol>
   <Subtype_Symbol id="SY2" name="Subtype_Symbol_PartyBis"><Subtype_SymbolProps>
    <Name>Subtype_Symbol_PartyBis</Name>
    <Relationships_Ref_Array><Relationships_Ref index="0">R9E</Relationships_Ref></Relationships_Ref_Array>
   </Subtype_SymbolProps></Subtype_Symbol>
  </Subtype_Symbol_Groups>
  <Hive_Database_Groups>
   <Hive_Database id="HD1" name="bcp_a"><Hive_DatabaseProps><Name>bcp_a</Name>
    <Dependent_Objects_Ref_Array><Dependent_Objects_Ref index="0">E1</Dependent_Objects_Ref>
     <Dependent_Objects_Ref index="1">E2</Dependent_Objects_Ref></Dependent_Objects_Ref_Array>
   </Hive_DatabaseProps></Hive_Database>
   <Hive_Database id="HD2" name="bcp_b"><Hive_DatabaseProps><Name>bcp_b</Name>
    <Dependent_Objects_Ref_Array><Dependent_Objects_Ref index="0">E3</Dependent_Objects_Ref></Dependent_Objects_Ref_Array>
   </Hive_DatabaseProps></Hive_Database>
  </Hive_Database_Groups>
  <Subject_Area_Groups>
   <Subject_Area id="SA1" name="Area"><Subject_AreaProps><Name>Area</Name>
    <Object_Order>0</Object_Order></Subject_AreaProps>
    <ER_Diagram id="DG1" name="Diag"><ER_DiagramProps><Name>Diag</Name>
     <Owner_Path>Modelo Subtipos.Area</Owner_Path></ER_DiagramProps>
     <ER_Model_Shape_Groups>
      <ER_Model_Shape id="S1" name="Party"><ER_Model_ShapeProps>
       <Model_Object_Ref>E1</Model_Object_Ref>
       <Owner_Path>Modelo Subtipos.Area.Diag</Owner_Path></ER_Model_ShapeProps></ER_Model_Shape>
      <ER_Model_Shape id="S2" name="Subtype_Symbol_Party"><ER_Model_ShapeProps>
       <Model_Object_Ref>SY1</Model_Object_Ref>
       <Owner_Path>Modelo Subtipos.Area.Diag</Owner_Path></ER_Model_ShapeProps></ER_Model_Shape>
      <ER_Model_Shape id="S3" name="Fantasma"><ER_Model_ShapeProps>
       <Model_Object_Ref>GHOST</Model_Object_Ref>
       <Owner_Path>Modelo Subtipos.Area.Diag</Owner_Path></ER_Model_ShapeProps></ER_Model_Shape>
     </ER_Model_Shape_Groups>
    </ER_Diagram>
   </Subject_Area>
  </Subject_Area_Groups>
 </Model>
</erwin>
""")


@pytest.fixture(scope="module")
def model(tmp_path_factory) -> ep.ErwinModel:
    p = tmp_path_factory.mktemp("erwin_sub") / "subtipos.xml"
    p.write_text(FIXTURE, encoding="utf-8")
    return ep.parse(str(p))


# ── parser ──────────────────────────────────────────────────────────────
def test_parsea_simbolos_y_sus_relaciones(model):
    assert set(model.subtype_symbols) == {"SY1", "SY2"}
    sy1 = model.subtype_symbols["SY1"]
    assert sy1.name == "Subtype_Symbol_Party" and sy1.rel_refs == ["R9"]
    assert model.subtype_symbols["SY2"].rel_refs == ["R9E"]


def test_mapa_relacion_a_simbolo(model):
    sym_of = model.subtype_symbol_of_rel()
    assert sym_of == {"R9": "SY1", "R9E": "SY2"}   # R9C queda huérfana


def test_relaciones_tipo_9_y_pares_heredados(model):
    assert model.relationships["R9"].rel_type == ep.REL_SUBTYPE
    pairs = model.fk_pairs()
    assert pairs["R9"] == [("A1", "B1")]
    assert pairs["R9C"] == [("A1", "C1")]
    assert pairs["R9E"] == [("C1", "B2")]


# ── quality gate ────────────────────────────────────────────────────────
def test_findings_de_subcategoria_y_duplicados(model):
    by_code = {x["code"]: x for x in analyze(model)}

    assert by_code["W-SUBTYPE-ORPHAN-REL"]["count"] == 1
    assert "R/9C" in by_code["W-SUBTYPE-ORPHAN-REL"]["items"][0]

    assert by_code["W-SUBTYPE-CHILD-MULTI"]["count"] == 1
    assert "INDIVIDUO en 2 grupos" in by_code["W-SUBTYPE-CHILD-MULTI"]["items"][0]

    # PARTY vive en bcp_a (E1) y bcp_b (E3): global sí, por-schema no.
    assert by_code["W-DUP-TABLE-GLOBAL"]["count"] == 1
    assert "PARTY" in by_code["W-DUP-TABLE-GLOBAL"]["items"][0]
    assert "W-DUP-TABLE" not in by_code

    assert by_code["W-DUP-LOGICAL-NAME"]["count"] == 1
    assert by_code["W-DUP-LOGICAL-NAME"]["items"] == ["Party ×2"]

    # las Type 9 entran al chequeo de integridad: acá están sanas
    assert "W-REL-BROKEN" not in by_code
    assert "W-REL-NO-PAIRS" not in by_code

    # el shape del símbolo ya NO es "muerto"; el fantasma sí
    assert by_code["I-SHAPE-DEAD"]["count"] == 1
    assert "GHOST" in by_code["I-SHAPE-DEAD"]["items"][0]


def test_volumetria_incluye_subtipos(model):
    s = summarize(model)
    assert s["relaciones_subtipo"] == 3
    assert s["simbolos_subcategoria"] == 2
    assert s["relaciones_tabla_tabla"] == 0


def test_relacion_tipo_9_rota_si_falta_extremo(tmp_path):
    roto = FIXTURE.replace("<Child_Entity_Ref>E2</Child_Entity_Ref>",
                           "<Child_Entity_Ref>NO-EXISTE</Child_Entity_Ref>", 1)
    p = tmp_path / "roto.xml"
    p.write_text(roto, encoding="utf-8")
    by_code = {x["code"]: x for x in analyze(ep.parse(str(p)))}
    assert by_code["W-REL-BROKEN"]["count"] == 1          # doc 109: advertencia (se omite)
    assert by_code["W-REL-BROKEN"]["severity"] == "WARN"
    assert "R/9" in by_code["W-REL-BROKEN"]["items"][0]
