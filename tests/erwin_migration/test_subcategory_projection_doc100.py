"""Doc 100 (7.1 del doc 98) — la regla R4 reconoce una subcategoría YA migrada.

El migrate prefetcheaba las relaciones vivas con una proyección SIN
`subcategory` y armaba la clave natural del lado de la BD con
`bool(r.get("subcategory"))` — siempre False —, mientras que la del XML sí
lleva el marcador: nunca calzaban. En Lakebase (que respeta la proyección) cada
re-corrida re-escribía todas las relaciones de subtipo, y otro archivo de la
misma familia metía la MISMA subcategoría repetida.

La BD falsa de los tests del kit ignora las proyecciones (por eso la suite no
lo veía): acá se usa una que devuelve SOLO los campos pedidos, como Lakebase."""
from __future__ import annotations

from scripts.erwin_migration import erwin_parser as ep
from scripts.erwin_migration.migrate import Migrator
from tests.erwin_migration.test_migrate_merge import FakeColl, FakeDb, attr, entity, rel


class ProjectingColl(FakeColl):
    """`find` devuelve sólo los campos proyectados (+ `_id`), como Lakebase y Mongo."""

    def find(self, flt=None, projection=None):
        docs = super().find(flt, projection)
        if not projection:
            return docs
        keep = {k for k, v in projection.items() if v} | {"_id"}
        return [{k: v for k, v in d.items() if k in keep} for d in docs]


class ProjectingDb(FakeDb):
    def __getitem__(self, name: str) -> ProjectingColl:
        return ProjectingColl(self.data.setdefault(name, {}))


def _modelo_subtipos(sfx: str = "") -> ep.ErwinModel:
    """Party con dos subtipos agrupados por un símbolo. `sfx` cambia TODOS los
    ids de Erwin: otro archivo de la misma familia trae las mismas tablas y
    columnas con ids propios."""
    m = ep.ErwinModel(name=f"M Subtipos{sfx}")
    e1 = entity(f"E1{sfx}", "PARTY", [attr(f"A1{sfx}", f"E1{sfx}", "CODPARTY", 1)], pk=(f"A1{sfx}",))
    e2 = entity(f"E2{sfx}", "INDIVIDUO", [
        attr(f"B1{sfx}", f"E2{sfx}", "CODPARTY", 1, parent_attr=f"A1{sfx}", parent_rel=f"R9{sfx}")],
        pk=(f"B1{sfx}",))
    e3 = entity(f"E3{sfx}", "ORGANIZACION", [
        attr(f"C1{sfx}", f"E3{sfx}", "CODPARTY", 1, parent_attr=f"A1{sfx}", parent_rel=f"R9B{sfx}")],
        pk=(f"C1{sfx}",))
    m.entities = {e.id: e for e in (e1, e2, e3)}
    m.relationships = {
        f"R9{sfx}": rel(f"R9{sfx}", f"E1{sfx}", f"E2{sfx}", rel_type=ep.REL_SUBTYPE),
        f"R9B{sfx}": rel(f"R9B{sfx}", f"E1{sfx}", f"E3{sfx}", rel_type=ep.REL_SUBTYPE),
    }
    m.subtype_symbols = {f"SY1{sfx}": ep.ErwinSubtypeSymbol(
        id=f"SY1{sfx}", name="Subtype_Symbol_Party", rel_refs=[f"R9{sfx}", f"R9B{sfx}"])}
    m.hive_dbs = {"S1": [f"E1{sfx}", f"E2{sfx}", f"E3{sfx}"]}
    return m


def test_la_recorrida_reusa_las_subcategorias_sin_reescribirlas():
    db = ProjectingDb()
    Migrator(db, _modelo_subtipos(), "Fam", None).run()
    assert len(db.data["relationships"]) == 2

    otra = Migrator(db, _modelo_subtipos(), "Fam", None)
    otra.run()
    assert otra.stats["relaciones reusadas (ya existían — R4)"] == 2
    assert otra.stats.get("relaciones", 0) == 0
    assert len(db.data["relationships"]) == 2


def test_otro_archivo_de_la_familia_no_repite_la_subcategoria():
    db = ProjectingDb()
    Migrator(db, _modelo_subtipos(), "Fam", None).run()

    archivo_2 = Migrator(db, _modelo_subtipos("-f2"), "Fam", None)
    archivo_2.run()
    assert archivo_2.stats["relaciones reusadas (ya existían — R4)"] == 2
    assert len(db.data["relationships"]) == 2
    assert all(r["subcategory"] is True for r in db.data["relationships"].values())
