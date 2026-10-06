"""Doc 110 — el migrate escribe EN COLA y, al terminar, espera lo encolado.

Con el puente en cola (`pipeline()`), `bulk_write` vuelve antes de escribir. El
archivo sólo se da por migrado cuando TODO quedó escrito: `flush_all` vacía la
cola (y propaga su error). El resultado final es el mismo que escribiendo de a
un lote esperando cada uno."""
from __future__ import annotations

from scripts.erwin_migration import migrate
from scripts.erwin_migration.migrate import Migrator
from tests.erwin_migration.test_diagram_style_doc109 import FIXTURE, _parse
from tests.erwin_migration.test_subcategory_projection_doc100 import ProjectingDb


class QueuedDb(ProjectingDb):
    """Como el puente en cola: `bulk_write` encola; leer o `drain()` lo escribe."""

    def __init__(self, data=None) -> None:
        super().__init__(data)
        self.pending: list[tuple[str, list]] = []
        self.pipelined = False

    def pipeline(self, workers: int = 3) -> None:
        self.pipelined = True

    def drain(self) -> None:
        for name, ops in self.pending:
            ProjectingDb.__getitem__(self, name).bulk_write(ops)
        self.pending.clear()

    def __getitem__(self, name):
        db, base = self, ProjectingDb.__getitem__(self, name)

        class Coll:
            def bulk_write(self, ops):
                db.pending.append((name, list(ops)))

            def find(self, *a, **k):
                db.drain()
                return base.find(*a, **k)

            def find_one(self, *a, **k):
                db.drain()
                return base.find_one(*a, **k)
        return Coll()


def _sin_fechas(data: dict) -> dict:
    return {coll: {i: {k: v for k, v in d.items() if k not in ("createdAt", "updatedAt")}
                   for i, d in docs.items()}
            for coll, docs in data.items() if docs}


def test_al_terminar_no_queda_nada_en_cola_y_el_resultado_es_el_de_siempre(tmp_path):
    model = _parse(tmp_path, FIXTURE, "m.xml")
    queued, plain = QueuedDb(), ProjectingDb()
    Migrator(queued, model, "P", None, source_folder="Origen").run()
    Migrator(plain, _parse(tmp_path, FIXTURE, "m.xml"), "P", None, source_folder="Origen").run()
    assert queued.pending == []
    names = sorted(t["physicalName"] for t in queued.data["canonical_tables"].values())
    assert len(names) == 3
    assert _sin_fechas(queued.data) == _sin_fechas(plain.data)


def test_main_activa_la_cola_del_puente(tmp_path, monkeypatch):
    path = tmp_path / "m.xml"
    path.write_text(FIXTURE, encoding="utf-8")
    db = QueuedDb()
    monkeypatch.setattr("app.core.db.sync.get_sync_db", lambda: db)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    rc = migrate.main([str(path), "--apply", "--force", "--project", "P",
                       "--report", str(tmp_path / "r.json")])
    assert rc == 0 and db.pipelined and db.pending == []
