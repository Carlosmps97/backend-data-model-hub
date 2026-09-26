"""Doc 95 D2: el parser limpia los nombres que trae el XML — U+00A0 y otros
espacios Unicode, caracteres de ancho cero y espacios repetidos. En MODELO DDV
dos nombres traían U+00A0 al final y el glosario los mostraba como renombres
«iguales» en cada apply."""
from __future__ import annotations

import textwrap

from scripts.erwin_migration import erwin_parser as ep

NS = 'xmlns="http://www.erwin.com/dm/data"'
NBSP, ZW = "\u00a0", "\u200b"

XML = textwrap.dedent(f"""\
<?xml version="1.0" encoding="UTF-8"?>
<erwin xmlns="http://www.erwin.com/dm" FileVersion="10.10" Format="erwin">
 <Model {NS} id="M1" name="Modelo">
  <ModelProps><Name>Modelo</Name></ModelProps>
  <Domain_Groups>
   <Domain id="D1" name="Codigo{NBSP}"><DomainProps><Name>Codigo{NBSP}</Name>
    <Physical_Data_Type>VARCHAR(30)</Physical_Data_Type>
    <User_Formatted_Physical_Name>CODIGO{NBSP}</User_Formatted_Physical_Name></DomainProps></Domain>
  </Domain_Groups>
  <Entity_Groups>
   <Entity id="E1" name="HM_JERARQUIA{NBSP}">
    <EntityProps><Name>HM_JERARQUIA{NBSP}</Name><Physical_Name>HM_JERARQUIA{NBSP}</Physical_Name></EntityProps>
    <Attribute_Groups>
     <Attribute id="A1" name="descripcion  grupo{ZW}{NBSP}"><AttributeProps><Name>descripcion grupo</Name>
      <User_Formatted_Physical_Name>DESGRUPO{NBSP}</User_Formatted_Physical_Name>
      <Physical_Data_Type>VARCHAR(20)</Physical_Data_Type><Physical_Order>1</Physical_Order></AttributeProps></Attribute>
    </Attribute_Groups>
   </Entity>
  </Entity_Groups>
  <View_Groups>
   <View id="V1" name="HM_JERARQUIA{NBSP}"><ViewProps><Name>HM_JERARQUIA{NBSP}</Name></ViewProps></View>
  </View_Groups>
 </Model>
</erwin>
""")


def test_clean_name_normaliza_espacios_unicode_e_invisibles():
    assert ep.clean_name("HM_JERARQUIAFUNCIONALCRE\u00a0") == "HM_JERARQUIAFUNCIONALCRE"
    assert ep.clean_name("descripcion\u00a0grupo  producto\u200b ") == "descripcion grupo producto"
    assert ep.clean_name("\u2007CODIGO\u202f") == "CODIGO"
    assert ep.clean_name(None) == "" and ep.clean_name("") == ""


def test_parse_limpia_tablas_columnas_vistas_y_dominios(tmp_path):
    p = tmp_path / "nbsp.xml"
    p.write_text(XML, encoding="utf-8")
    m = ep.parse(str(p))
    e = m.entities["E1"]
    assert (e.name, e.physical) == ("HM_JERARQUIA", "HM_JERARQUIA")
    (a,) = e.attributes
    assert (a.name, a.physical) == ("descripcion grupo", "DESGRUPO")
    assert m.views["V1"].name == "HM_JERARQUIA"
    assert (m.domains["D1"].name, m.domains["D1"].physical_name) == ("Codigo", "CODIGO")
