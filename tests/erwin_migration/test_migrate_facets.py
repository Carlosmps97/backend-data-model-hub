"""Doc 69: la migración escribe AMBAS facetas — UDPs lógicos y físicos en el
mismo `udpValues` (ids distintos), tipo/orden/existencia lógicos, y corrige el
override de tipo: físico de la columna vs físico del DOMINIO (antes comparaba
contra el lógico del dominio y marcaba miles de falsos overrides)."""
from __future__ import annotations

from scripts.erwin_migration import erwin_parser as ep
from scripts.erwin_migration import policies as pol
from scripts.erwin_migration.migrate import Migrator

from test_migrate_override import FakeDb  # mismo dir de tests (sin __init__.py): import por basename


def _attr(aid, owner, logical, phys, order, *, ptype="STRING", ltype="", dom=None,
          corder=None, lonly=False, definition="", comment=""):
    return ep.ErwinAttribute(
        id=aid, owner_id=owner, owner_kind="Entity", name=logical, physical=phys, physical_raw=phys,
        data_type=ptype, logical_type=ltype, nullable=True, order=order, definition=definition, comment=comment,
        domain_ref=dom, parent_attr_ref=None, parent_rel_ref=None,
        column_order=order if corder is None else corder, logical_only=lonly)


def _udp(did, owner, mode, name, code="6", allowed=()):
    return ep.ErwinUdpDef(id=did, full_name=f"{owner}.{mode}.{name}", owner_class=owner, view_mode=mode,
                          short_name=name, data_type_code=code, default="No Definido", allowed_values=list(allowed))


def _modelo():
    m = ep.ErwinModel(name="Modelo Facetas")
    m.domains = {
        # físico de Erwin == derivado (join+UPPER: CODIGO) ⇒ physicalName None; Comment == Definition ⇒ None
        "D1": ep.ErwinDomain(id="D1", name="Codigo", builtin=False, data_type="VARCHAR(20)", parent_ref=None,
                             definition="Codigos", physical_type="VARCHAR(30)", physical_name="CODIGO", comment="Codigos"),
        # físico de Erwin distinto del derivado (CODIGOCLAVE) ⇒ override; Comment distinto ⇒ descripción física
        "D2": ep.ErwinDomain(id="D2", name="Codigo Clave", builtin=False, data_type="VARCHAR(20)", parent_ref=None,
                             definition="Clave.", physical_type="VARCHAR(30)", physical_name="CodigoClave", comment="Clave fisica."),
    }
    e1 = ep.ErwinEntity(id="E1", name="cliente", physical="CLIENTE", physical_was_macro=False, definition="",
                        comment="", attributes=[
                            # hereda el FÍSICO del dominio ⇒ NO es override (antes: falso override)
                            _attr("A1", "E1", "codigo", "COD", 1, ptype="VARCHAR(30)", ltype="VARCHAR(20)", dom="D1", corder=3,
                                  definition="Def A1", comment="Comentario A1"),
                            # difiere del físico del dominio ⇒ override real; lógico distinto ⇒ override lógico
                            _attr("A2", "E1", "codigo alt", "CODALT", 2, ptype="STRING", ltype="CHAR(20)", dom="D1", corder=1,
                                  definition="Igual", comment="Igual"),
                            _attr("A3", "E1", "nota", "NOTA", 3, ptype="STRING", ltype="", corder=2, lonly=True,
                                  definition="", comment="solo comment"),
                        ], pk_attr_ids=set(), pk_attr_order=[], physical_only=True)
    m.entities = {"E1": e1}
    m.hive_dbs = {"S1": ["E1"]}
    m.udp_defs = {
        "UP": _udp("UP", "Entity", "Physical", "Clasificacion del Dato", allowed=["No Definido", "No DAC", "DAC"]),
        "UL": _udp("UL", "Entity", "Logical", "Clasificacion del Dato", allowed=["No Definido", "No DAC", "DAC"]),
        "AC": _udp("AC", "Attribute", "Logical", "Atributo Cross", allowed=["No Definido", "Si", "No"]),
        "CC": _udp("CC", "Attribute", "Physical", "Campo Cross", allowed=["No Definido", "Si", "No"]),
    }
    m.udp_values = [("E1", "Entity", "UP", "DAC"), ("E1", "Entity", "UL", "No DAC"), ("A1", "Attribute", "AC", "Si"),
                    ("D2", "Domain", "AC", "No"), ("D2", "Domain", "CC", "No")]   # doc 85: UDP por defecto del dominio
    return m


def _run():
    db = FakeDb()
    Migrator(db, _modelo(), "Proyecto F", None).run()
    return db


# Doc 75 D11: ids de las defs fijas NAMESPACEADOS por proyecto (cada proyecto
# tiene su catálogo); dentro del proyecto siguen siendo deterministas.
_PF = pol.platform_id("project|Proyecto F")
_UDP_PHYS_TABLE = pol.project_scoped_id(_PF, "udpfix|table|Clasificacion del Dato")
_UDP_LOG_TABLE = pol.project_scoped_id(_PF, "udpfix|table|logical|Clasificacion del Dato")
_UDP_LOG_COLUMN = pol.project_scoped_id(_PF, "udpfix|column|logical|Atributo Cross")
_UDP_PHYS_COLUMN_CC = pol.project_scoped_id(_PF, "udpfix|column|Campo Cross")


def test_defs_udp_sembradas_por_faceta_con_ids_estables():
    db = _run()
    defs = list(db.data["udp_definitions"].values())
    assert len(defs) == 25
    phys = next(d for d in defs if d["name"] == "Clasificacion del Dato" and d["level"] == "table" and d["view"] == "physical")
    log = next(d for d in defs if d["name"] == "Clasificacion del Dato" and d["level"] == "table" and d["view"] == "logical")
    assert phys["_id"] == _UDP_PHYS_TABLE
    assert log["_id"] == _UDP_LOG_TABLE
    assert all(d["projectId"] == _PF for d in defs)
    assert next(d for d in defs if d["level"] == "view" and d["name"] == "Filtro Despliegue 2021")["view"] == "physical"
    # re-run: mismos ids (idempotente dentro del proyecto)
    Migrator(db, _modelo(), "Proyecto F", None).run()
    assert len(db.data["udp_definitions"]) == 25


def test_valores_udp_de_ambas_facetas_en_el_mismo_udpvalues():
    db = _run()
    t = next(iter(db.data["canonical_tables"].values()))
    assert t["udpValues"] == {_UDP_PHYS_TABLE: "DAC", _UDP_LOG_TABLE: "No DAC"}
    assert t["physicalOnly"] is True and t["logicalOnly"] is False
    cols = {c["physicalName"]: c for c in db.data["canonical_columns"].values()}
    assert cols["COD"]["udpValues"] == {_UDP_LOG_COLUMN: "Si"}


def test_dominio_con_tipo_fisico_y_logico():
    db = _run()
    d = next(x for x in db.data["parent_domains"].values() if x["name"] == "Codigo")
    assert (d["defaultDataType"], d["logicalDataType"]) == ("VARCHAR(30)", "VARCHAR(20)")


def test_dominio_faceta_fisica_y_udp_por_defecto_doc85():
    db = _run()
    doms = {d["name"]: d for d in db.data["parent_domains"].values()}
    assert doms["Codigo"]["physicalName"] is None and doms["Codigo"]["physicalDescription"] is None
    assert doms["Codigo"]["udpValues"] == {}
    assert doms["Codigo Clave"]["physicalName"] == "CodigoClave"
    assert doms["Codigo Clave"]["physicalDescription"] == "Clave fisica."
    assert doms["Codigo Clave"]["udpValues"] == {_UDP_LOG_COLUMN: "No", _UDP_PHYS_COLUMN_CC: "No"}


def test_columna_physical_description_solo_si_difiere_doc85():
    db = _run()
    cols = {c["physicalName"]: c for c in db.data["canonical_columns"].values()}
    assert (cols["COD"]["description"], cols["COD"]["physicalDescription"]) == ("Def A1", "Comentario A1")
    assert cols["CODALT"]["physicalDescription"] is None                 # Comment == Definition
    assert (cols["NOTA"]["description"], cols["NOTA"]["physicalDescription"]) == ("solo comment", None)


def test_extract_standards_expone_fisico_del_dominio_doc85():
    from scripts.erwin_migration.extract_standards import extract
    doms = {d["name"]: d for d in extract(_modelo())["parent_domains"]}
    assert doms["Codigo Clave"]["physicalName"] == "CodigoClave" and doms["Codigo Clave"]["comment"] == "Clave fisica."


def test_override_por_faceta_y_campos_logicos():
    db = _run()
    cols = {c["physicalName"]: c for c in db.data["canonical_columns"].values()}
    assert cols["COD"]["typeOverridden"] is False                 # VARCHAR(30) == físico del dominio
    assert cols["COD"]["logicalTypeOverridden"] is False          # VARCHAR(20) == lógico del dominio
    assert cols["COD"]["logicalDataType"] == "VARCHAR(20)"
    assert cols["CODALT"]["typeOverridden"] is True               # STRING ≠ VARCHAR(30)
    assert cols["CODALT"]["logicalTypeOverridden"] is True        # CHAR(20) ≠ VARCHAR(20)
    assert cols["NOTA"]["logicalDataType"] is None and cols["NOTA"]["logicalOnly"] is True


def test_orden_unico_sin_llaves_es_el_column_order_doc74():
    """Doc 74: un solo `ordinal` (el mismo en lógico y físico) = Column order de
    Erwin (CODALT, NOTA, COD); el orden físico de la BD (COD, CODALT, NOTA) ya
    no se persiste como orden aparte."""
    db = _run()
    cols = {c["physicalName"]: c for c in db.data["canonical_columns"].values()}
    assert [cols[n]["ordinal"] for n in ("COD", "CODALT", "NOTA")] == [2, 0, 1]
    assert not any(k in c for c in cols.values() for k in ("logicalOrdinal", "columnOrdinal"))


def test_orden_unico_llaves_primero_en_el_orden_de_la_llave_doc74():
    """«Físico normal» del owner: las PK van al inicio aunque el Column order de
    Erwin las tenga al final; entre PKs manda el orden de la llave."""
    m = ep.ErwinModel(name="Modelo Llaves")
    e = ep.ErwinEntity(id="E9", name="party", physical="PARTY", physical_was_macro=False, definition="",
                       comment="", attributes=[
                           _attr("P1", "E9", "codmes", "CODMES", 1, corder=3),
                           _attr("P2", "E9", "flag", "FLG", 2, corder=0),
                           _attr("P3", "E9", "codclave", "CODCLAVE", 3, corder=2),
                           _attr("P4", "E9", "nota", "NOTA", 4, corder=1),
                       ], pk_attr_ids={"P1", "P3"}, pk_attr_order=["P3", "P1"])
    m.entities = {"E9": e}
    m.hive_dbs = {"S1": ["E9"]}
    db = FakeDb()
    Migrator(db, m, "Proyecto K", None).run()
    cols = {c["physicalName"]: c for c in db.data["canonical_columns"].values()}
    assert [cols[n]["ordinal"] for n in ("CODCLAVE", "CODMES", "FLG", "NOTA")] == [0, 1, 2, 3]
    assert (cols["CODCLAVE"]["pkPosition"], cols["CODMES"]["pkPosition"], cols["FLG"]["pkPosition"]) == (0, 1, None)


def test_dominio_estandar_hereda_nombre_y_definicion_doc79():
    """Doc 79: un dominio "atributo estándar" (Erwin `Attribute_Definition`) se
    marca `inheritsName=True` y siembra su definición de atributo como
    `description`; un dominio genérico de tipo NO se marca y conserva su propia
    definición de dominio (la UI la muestra, pero jamás se empuja a columnas)."""
    m = ep.ErwinModel(name="Modelo Doc79")
    m.domains = {
        "D-GEN": ep.ErwinDomain(id="D-GEN", name="Codigo", builtin=False, data_type="VARCHAR(20)",
                                parent_ref=None, definition="Se asigna a atributos con codificacion.",
                                physical_type="VARCHAR(30)"),
        "D-STD": ep.ErwinDomain(id="D-STD", name="FecRutina", builtin=False, data_type="DATE",
                                parent_ref=None, definition="", physical_type="DATE",
                                attribute_definition="Fecha de la rutina."),
    }
    e = ep.ErwinEntity(id="E1", name="t", physical="T", physical_was_macro=False, definition="",
                       comment="", attributes=[], pk_attr_ids=set(), pk_attr_order=[])
    m.entities = {"E1": e}
    m.hive_dbs = {"S1": ["E1"]}
    db = FakeDb()
    Migrator(db, m, "Proyecto D79", None).run()
    doms = {d["name"]: d for d in db.data["parent_domains"].values()}
    assert doms["FecRutina"]["inheritsName"] is True
    assert doms["FecRutina"]["description"] == "Fecha de la rutina."
    assert doms["Codigo"]["inheritsName"] is False
    assert doms["Codigo"]["description"] == "Se asigna a atributos con codificacion."
