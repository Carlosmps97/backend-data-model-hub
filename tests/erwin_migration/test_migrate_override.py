"""Doc 68: la migración estampa `physicalNameOverridden` comparando el físico
real del XML contra `physicalize(lógico)` con las reglas vigentes por scope
(mappings en memoria + naming_config de BD con default join+UPPER). Un físico
custom (prefijo/`_` manual) queda protegido del rephysicalize retroactivo.

BD fake mínima (superficie find / find_one / bulk_write — mismo patrón que
test_migrate_merge)."""
from __future__ import annotations

from scripts.erwin_migration import erwin_parser as ep
from scripts.erwin_migration.migrate import Migrator


def _match(doc: dict, flt: dict) -> bool:
    for k, cond in (flt or {}).items():
        val = doc.get(k)
        if isinstance(cond, dict):
            if "$ne" in cond and val == cond["$ne"]:
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
    def __init__(self, data=None):
        self.data = data or {}

    def __getitem__(self, name):
        return FakeColl(self.data.setdefault(name, {}))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


def _attr(aid, owner, logical, phys, order):
    return ep.ErwinAttribute(
        id=aid, owner_id=owner, owner_kind="Entity", name=logical,
        physical=phys, physical_raw=phys, data_type="STRING", logical_type="",
        nullable=True, order=order, definition="", comment="",
        domain_ref=None, parent_attr_ref=None, parent_rel_ref=None)


def _entity(eid, logical, phys, attrs):
    return ep.ErwinEntity(id=eid, name=logical, physical=phys,
                          physical_was_macro=False, definition="", comment="",
                          attributes=attrs, pk_attr_ids=set(), pk_attr_order=[])


def _modelo():
    m = ep.ErwinModel(name="Modelo Override")
    e1 = _entity("E1", "maestro clientes", "MAESTROCLIENTES", [
        # lógico «codigo» + término column codigo→COD ⇒ derivado COD == físico.
        _attr("A1", "E1", "codigo", "COD", 1),
        # físico con «_» manual ⇒ custom.
        _attr("A2", "E1", "codigo", "COD_K", 2),
    ])
    # tabla con prefijo manual HD_ ⇒ custom (el derivado sería BASEEEFF).
    e2 = _entity("E2", "base eeff", "HD_BASEEEFF", [])
    # scope table: término tabla→TBL ⇒ TBLVENTAS es derivado (no override).
    e3 = _entity("E3", "tabla ventas", "TBLVENTAS", [])
    m.entities = {e.id: e for e in (e1, e2, e3)}
    m.hive_dbs = {"S1": ["E1", "E2", "E3"]}
    return m


def _db():
    # Doc 75: el glosario es del proyecto; los tests crean «Proyecto X» /
    # «Proyecto Y» con ids fijos para sembrar sus estándares.
    return FakeDb({
        "projects": {"proj-x": {"_id": "proj-x", "name": "Proyecto X"},
                     "proj-y": {"_id": "proj-y", "name": "Proyecto Y"}},
        "glossary_terms": {
            "g1": {"_id": "g1", "term": "codigo", "abbrev": "COD", "scope": "column", "projectId": "proj-x"},
            "g2": {"_id": "g2", "term": "tabla", "abbrev": "TBL", "scope": "table", "projectId": "proj-x"},
            # sin scope persistido ⇒ column (compat con list_entries del backend).
            "g3": {"_id": "g3", "term": "monto", "abbrev": "MTO", "projectId": "proj-x"},
        }})


def _tables(db):
    return {t["physicalName"]: t for t in db.data["canonical_tables"].values()}


def _cols(db):
    return {c["physicalName"]: c for c in db.data["canonical_columns"].values()}


def test_estampa_override_en_tablas_y_columnas():
    db = _db()
    mig = Migrator(db, _modelo(), "Proyecto X", None)
    mig.run()
    t = _tables(db)
    assert t["MAESTROCLIENTES"]["physicalNameOverridden"] is False  # join+UPPER
    assert t["HD_BASEEEFF"]["physicalNameOverridden"] is True       # prefijo manual
    assert t["TBLVENTAS"]["physicalNameOverridden"] is False        # término scope table
    c = _cols(db)
    assert c["COD"]["physicalNameOverridden"] is False
    assert c["COD_K"]["physicalNameOverridden"] is True
    assert mig.stats["tablas con físico custom (override)"] == 1
    assert mig.stats["columnas con físico custom (override)"] == 1


def test_naming_config_de_bd_manda_sobre_el_default():
    # Con separador «_» configurado, COD_K pasa a ser DERIVABLE… pero acá el
    # lógico es una sola palabra ⇒ probamos con la tabla: «base eeff» + «_»
    # deriva BASE_EEFF ⇒ HD_BASEEEFF sigue custom y BASE_EEFF sería derivado.
    db = _db()
    # Doc 75: la config de naming es POR PROYECTO (`<pid>:<scope>`).
    db.data["projects"] = {"proj-y": {"_id": "proj-y", "name": "Proyecto Y"}}
    db.data["naming_config"] = {
        "proj-y:table": {"_id": "proj-y:table", "projectId": "proj-y", "scope": "table",
                         "separator": "_", "case": "upper"},
        "proj-y:column": {"_id": "proj-y:column", "projectId": "proj-y", "scope": "column",
                          "separator": "_", "case": "upper"},
    }
    m = ep.ErwinModel(name="M2")
    m.entities = {"E1": _entity("E1", "base eeff", "BASE_EEFF", []),
                  "E2": _entity("E2", "base eeff dos", "BASEEEFFDOS", [])}
    m.hive_dbs = {"S1": ["E1", "E2"]}
    mig = Migrator(db, m, "Proyecto Y", None)
    mig.run()
    t = _tables(db)
    assert t["BASE_EEFF"]["physicalNameOverridden"] is False
    assert t["BASEEEFFDOS"]["physicalNameOverridden"] is True  # regla con «_»
