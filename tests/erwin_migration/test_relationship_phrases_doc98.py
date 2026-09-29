"""Frases de relación en el kit Erwin (doc 98): el parser lee
`Parent_To_Child_Verb_Phrase` / `Child_To_Parent_Verb_Phrase`, el migrate las
escribe en la relación y el extract las saca en relationships.json/csv.

Censo real (UDV INT LOGICO, 2026-09-27): 372 / 290 relaciones con frase."""
from __future__ import annotations

import csv
import textwrap

import pytest

from scripts.erwin_migration import erwin_parser as ep
from scripts.erwin_migration.extract_model import _flat_csvs, extract
from scripts.erwin_migration.migrate import Migrator
from tests.erwin_migration.test_migrate_merge import FakeDb, attr, entity, rel

NS = 'xmlns="http://www.erwin.com/dm/data"'

XML = textwrap.dedent(f"""\
<?xml version="1.0" encoding="UTF-8"?>
<erwin xmlns="http://www.erwin.com/dm" FileVersion="10.10" Format="erwin">
 <Model {NS} id="M1" name="Modelo Frases">
  <ModelProps><Name>Modelo Frases</Name></ModelProps>
  <Relationship_Groups>
   <Relationship id="R1" name="R/1"><RelationshipProps><Type>7</Type><Cardinality>-3</Cardinality>
    <Parent_Entity_Ref>E1</Parent_Entity_Ref><Child_Entity_Ref>E2</Child_Entity_Ref>
    <Parent_To_Child_Verb_Phrase>Persona tiene</Parent_To_Child_Verb_Phrase>
    <Child_To_Parent_Verb_Phrase>  un Estado Civil </Child_To_Parent_Verb_Phrase>
   </RelationshipProps></Relationship>
   <Relationship id="R2" name="R/2"><RelationshipProps><Type>2</Type><Cardinality>-3</Cardinality>
    <Parent_Entity_Ref>E1</Parent_Entity_Ref><Child_Entity_Ref>E2</Child_Entity_Ref>
    <Child_To_Parent_Verb_Phrase>a una Industria</Child_To_Parent_Verb_Phrase>
   </RelationshipProps></Relationship>
   <Relationship id="R3" name="R/3"><RelationshipProps><Type>7</Type><Cardinality>-3</Cardinality>
    <Parent_Entity_Ref>E1</Parent_Entity_Ref><Child_Entity_Ref>E2</Child_Entity_Ref>
    <Parent_To_Child_Verb_Phrase>   </Parent_To_Child_Verb_Phrase>
   </RelationshipProps></Relationship>
  </Relationship_Groups>
 </Model>
</erwin>
""")


@pytest.fixture(scope="module")
def parsed(tmp_path_factory) -> ep.ErwinModel:
    p = tmp_path_factory.mktemp("erwin98") / "frases.xml"
    p.write_text(XML, encoding="utf-8")
    return ep.parse(str(p))


# ── parser ──────────────────────────────────────────────────────────────
def test_parser_lee_las_dos_frases_recortadas(parsed):
    r1 = parsed.relationships["R1"]
    assert r1.parent_to_child_phrase == "Persona tiene"
    assert r1.child_to_parent_phrase == "un Estado Civil"


def test_parser_una_sola_direccion(parsed):
    r2 = parsed.relationships["R2"]
    assert r2.parent_to_child_phrase == ""
    assert r2.child_to_parent_phrase == "a una Industria"


def test_parser_frase_en_blanco_es_vacia(parsed):
    r3 = parsed.relationships["R3"]
    assert r3.parent_to_child_phrase == "" and r3.child_to_parent_phrase == ""


# ── migrate ─────────────────────────────────────────────────────────────
def _modelo(p2c: str = "", c2p: str = "") -> ep.ErwinModel:
    """TAB_P (PK CODP) → TAB_H (FK CODP) con una relación R1."""
    m = ep.ErwinModel(name="Modelo Frases")
    e1 = entity("E1", "TAB_P", [attr("A1", "E1", "CODP", 1)], pk=("A1",))
    e2 = entity("E2", "TAB_H", [
        attr("B1", "E2", "CODH", 1),
        attr("B2", "E2", "CODP", 2, parent_attr="A1", parent_rel="R1")], pk=("B1",))
    m.entities = {e.id: e for e in (e1, e2)}
    r1 = rel("R1", "E1", "E2")
    r1.parent_to_child_phrase, r1.child_to_parent_phrase = p2c, c2p
    m.relationships = {"R1": r1}
    m.hive_dbs = {"S1": ["E1", "E2"]}
    return m


def _migrada(db: FakeDb, mig: Migrator) -> dict:
    return db.data["relationships"][mig.pid("R1")]


def test_migrate_escribe_las_frases_del_xml():
    db = FakeDb()
    mig = Migrator(db, _modelo("Persona tiene", "un Estado Civil"), "Proyecto Frases", None)
    mig.run()
    doc = _migrada(db, mig)
    assert doc["parentToChildPhrase"] == "Persona tiene"
    assert doc["childToParentPhrase"] == "un Estado Civil"


def test_migrate_una_sola_direccion_no_inventa_la_otra():
    db = FakeDb()
    mig = Migrator(db, _modelo(c2p="a una Industria"), "Proyecto Frases", None)
    mig.run()
    doc = _migrada(db, mig)
    assert doc["childToParentPhrase"] == "a una Industria"
    assert "parentToChildPhrase" not in doc          # ni siquiera viaja en el $set


def test_xml_sin_frase_no_borra_la_escrita_en_la_plataforma():
    """La relación ya existe con el MISMO id pero sus pares apuntan a columnas
    que ya no están (no entra al dedup R4) → el migrate la re-escribe. El XML
    sin frase NO debe llevarse la frase que un modelador escribió a mano."""
    db = FakeDb()
    probe = Migrator(db, _modelo(), "Proyecto Frases", None)
    rid, pid = probe.pid("R1"), probe.project_id
    db.data["projects"] = {pid: {"_id": pid, "name": "Proyecto Frases"}}
    db.data["relationships"] = {rid: {
        "_id": rid, "projectId": pid, "parentTableId": "vieja-p", "childTableId": "vieja-h",
        "pairs": [{"parentColumnId": "col-ida", "childColumnId": "col-ida-2"}],
        "childToParentPhrase": "escrita en la plataforma", "flgactive": True}}
    mig = Migrator(db, _modelo(), "Proyecto Frases", None)
    mig.run()
    doc = _migrada(db, mig)
    assert doc["parentTableId"] == mig.pid("E1")            # el XML sí pisó el modelo
    assert doc["childToParentPhrase"] == "escrita en la plataforma"


def test_xml_con_frase_pisa_la_de_la_plataforma():
    db = FakeDb()
    probe = Migrator(db, _modelo(), "Proyecto Frases", None)
    rid, pid = probe.pid("R1"), probe.project_id
    db.data["projects"] = {pid: {"_id": pid, "name": "Proyecto Frases"}}
    db.data["relationships"] = {rid: {
        "_id": rid, "projectId": pid, "parentTableId": "vieja-p", "childTableId": "vieja-h",
        "pairs": [{"parentColumnId": "col-ida", "childColumnId": "col-ida-2"}],
        "childToParentPhrase": "escrita en la plataforma", "flgactive": True}}
    mig = Migrator(db, _modelo(c2p="un Estado Civil"), "Proyecto Frases", None)
    mig.run()
    assert _migrada(db, mig)["childToParentPhrase"] == "un Estado Civil"


def test_recorrida_salta_la_relacion_existente_y_respeta_la_frase_de_la_plataforma():
    """R4 (doc 32b): en una re-corrida la relación ya migrada se REUSA — el
    migrate ni la toca. La frase editada en la plataforma sobrevive aunque el
    XML traiga otra."""
    db = FakeDb()
    primera = Migrator(db, _modelo("Persona tiene", "un Estado Civil"), "Proyecto Frases", None)
    primera.run()
    _migrada(db, primera)["parentToChildPhrase"] = "editada en la plataforma"

    segunda = Migrator(db, _modelo("Persona tiene", "un Estado Civil"), "Proyecto Frases", None)
    segunda.run()
    assert segunda.stats["relaciones reusadas (ya existían — R4)"] == 1
    assert segunda.stats.get("relaciones", 0) == 0
    assert _migrada(db, segunda)["parentToChildPhrase"] == "editada en la plataforma"


def test_migrate_escribe_las_frases_de_una_relacion_de_subtipo():
    """UDV INT LOGICO trae 28 relaciones Type 9 con frase (una por RAMA del
    símbolo): 'Party es un Individuo', 'Party es una Organizacion'…"""
    m = ep.ErwinModel(name="Modelo Frases")
    e1 = entity("E1", "PARTY", [attr("A1", "E1", "CODPARTY", 1)], pk=("A1",))
    e2 = entity("E2", "INDIVIDUO", [attr("B1", "E2", "CODPARTY", 1, parent_attr="A1", parent_rel="R9")], pk=("B1",))
    e3 = entity("E3", "ORGANIZACION", [attr("C1", "E3", "CODPARTY", 1, parent_attr="A1", parent_rel="R9B")], pk=("C1",))
    m.entities = {e.id: e for e in (e1, e2, e3)}
    r9, r9b = rel("R9", "E1", "E2", rel_type=ep.REL_SUBTYPE), rel("R9B", "E1", "E3", rel_type=ep.REL_SUBTYPE)
    r9.parent_to_child_phrase = "Party es un Individuo"
    r9b.parent_to_child_phrase, r9b.child_to_parent_phrase = "Organizacion es un tipo de", "Party"
    m.relationships = {"R9": r9, "R9B": r9b}
    m.subtype_symbols = {"SY1": ep.ErwinSubtypeSymbol(id="SY1", name="Subtype_Symbol_Party", rel_refs=["R9", "R9B"])}
    m.hive_dbs = {"S1": ["E1", "E2", "E3"]}

    db = FakeDb()
    mig = Migrator(db, m, "Proyecto Frases", None)
    mig.run()
    a, b = db.data["relationships"][mig.pid("R9")], db.data["relationships"][mig.pid("R9B")]
    assert a["subcategory"] is True and a["parentToChildPhrase"] == "Party es un Individuo"
    assert "childToParentPhrase" not in a
    assert (b["parentToChildPhrase"], b["childToParentPhrase"]) == ("Organizacion es un tipo de", "Party")


# ── extract ─────────────────────────────────────────────────────────────
def test_extract_saca_las_frases_en_relationships():
    data, _dropped = extract(_modelo("Persona tiene", "un Estado Civil"))
    (r,) = data["relationships"]
    assert r["parentToChildPhrase"] == "Persona tiene"
    assert r["childToParentPhrase"] == "un Estado Civil"


def test_extract_sin_frase_deja_null():
    data, _dropped = extract(_modelo())
    (r,) = data["relationships"]
    assert r["parentToChildPhrase"] is None and r["childToParentPhrase"] is None


def test_extract_csv_lleva_las_frases_por_fila(tmp_path):
    data, _dropped = extract(_modelo("Persona tiene", "un Estado Civil"))
    _flat_csvs(tmp_path, data)
    with (tmp_path / "relationships.csv").open(encoding="utf-8-sig", newline="") as f:
        (row,) = list(csv.DictReader(f))
    assert row["parentColumn"] == "CODP" and row["childColumn"] == "CODP"
    assert row["parentToChildPhrase"] == "Persona tiene"
    assert row["childToParentPhrase"] == "un Estado Civil"
