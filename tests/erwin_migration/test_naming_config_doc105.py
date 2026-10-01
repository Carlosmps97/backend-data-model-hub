"""Doc 105 (revisión R5, punto 4) — el kit lee `naming_config` DIRECTO de la
BD: un valor que el motor no puede usar (p. ej. `case: 'Upper'`, que puede
volver por un rollback viejo) abortaba la carga con ValueError en la primera
columna. Ahora el kit normaliza igual que la app al leer (valor inválido →
default corporativo del scope)."""
from __future__ import annotations

from scripts.erwin_migration import policies as pol
from scripts.erwin_migration.migrate import Migrator
from tests.erwin_migration.test_canvas_merge_doc105 import _archivo
from tests.erwin_migration.test_subcategory_projection_doc100 import ProjectingDb


def _db_con_naming(**column) -> tuple[ProjectingDb, str]:
    db = ProjectingDb()
    pid = pol.platform_id("project|Familia")
    db.data["projects"] = {pid: {"_id": pid, "name": "Familia"}}
    db.data["naming_config"] = {f"{pid}:column": {"_id": f"{pid}:column", "projectId": pid, "scope": "column",
                                                  **column}}
    return db, pid


def test_el_kit_usa_la_regla_efectiva_ante_un_naming_invalido():
    db, _pid = _db_con_naming(separator=5, case="Upper")
    mig = Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None)
    assert mig.naming_cfg["column"] == ("", "upper")                             # default corporativo
    mig.run()                                                                    # no aborta
    assert mig.stats["tablas"] == 1


def test_el_kit_respeta_un_naming_valido():
    db, _pid = _db_con_naming(separator="_", case="lower")
    assert Migrator(db, _archivo("1", "TAB_UNO"), "Familia", None).naming_cfg["column"] == ("_", "lower")
