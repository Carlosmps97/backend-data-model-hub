"""Doc 105 — Reporting sobre una versión propia (diferidos del doc 102).

- A3: `overlay_views` ignoraba `schema` cuando también llegaba `tableIds` (la
  lectura publicada sí aplica los dos): una vista del draft de OTRO esquema se
  colaba en el Database Explorer.
- A4: `relationships_report` cortaba con `limit` ANTES del overlay: con altas y
  bajas en la versión salían más filas que el tope o faltaban las que sí
  entraban.
- A7: la cabecera de la versión se leía dos veces por request."""
from __future__ import annotations

import asyncio

import pytest

from app.core.db import client as db_client
from app.features.changesets import repository as cs_repo
from app.features.reporting import draft
from app.features.reporting import views as rep_views
from tests.support.fakedb import FakeDb

from .helpers import P, ch


def _up(**payload):
    return {"op": "upsert", "payload": {"projectId": P, **payload}}


# ── A3 ──────────────────────────────────────────────────────────────────────
def test_a3_alta_de_la_version_en_otro_esquema_no_entra():
    altas = {"v8": _up(schema="core_vu", sourceTableIds=["t1"]),       # cumple los dos filtros
             "v9": _up(schema="otro_vu", sourceTableIds=["t1"])}       # sólo la tabla
    assert [v["id"] for v in draft.overlay_views([], altas, P, {"t1"}, "core_vu")] == ["v8"]


def test_a3_vista_publicada_que_la_version_muda_de_esquema_sale():
    pub = [{"id": "v1", "projectId": P, "schema": "core_vu", "sourceTableIds": ["t1"]}]
    mudada = {"v1": _up(schema="movida_vu", sourceTableIds=["t1"])}
    assert draft.overlay_views(pub, mudada, P, {"t1"}, "core_vu") == []


# ── A4 ──────────────────────────────────────────────────────────────────────
def _rel(rid: str, parent: str, child: str) -> dict:
    return {"_id": rid, "projectId": P, "flgactive": True, "parentTableId": parent, "childTableId": child,
            "pairs": [{"parentColumnId": "c1", "childColumnId": "c2"}]}


@pytest.fixture
def rels_db(monkeypatch) -> FakeDb:
    """Producción: r1..r3 (una fila por relación). El draft `cs1` se arma en cada test."""
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    fake.raw["changesets"].insert_one({"_id": "cs1", "title": "v2", "owner": "ana", "projectId": P,
                                       "status": "draft"})
    fake.raw["relationships"].insert_many([_rel("r1", "t1", "t2"), _rel("r2", "t2", "t3"), _rel("r3", "t3", "t4")])
    return fake


def _version_ids(fake: FakeDb, limit: int, *changes: dict) -> list[str]:
    fake.raw["changeset_changes"].insert_many(list(changes))
    ch_map = asyncio.run(cs_repo.changes_map("cs1", ["relationships", "canonical_tables", "canonical_columns"]))
    return [r["id"] for r in asyncio.run(rep_views.relationships_report(P, limit=limit, changes=ch_map))]


def _alta(rid: str) -> dict:
    return ch("relationships", rid, "upsert", {"parentTableId": "t1", "childTableId": "t3",
                                               "pairs": [{"parentColumnId": "c1", "childColumnId": "c2"}]})


def test_a4_las_altas_no_pasan_el_tope(rels_db):
    """Las primeras `limit` de la VERSIÓN (orden por id), no `limit` + altas."""
    assert _version_ids(rels_db, 2, _alta("r0")) == ["r0", "r1"]


def test_a4_una_baja_dentro_del_tope_no_deja_fuera_a_la_siguiente(rels_db):
    assert _version_ids(rels_db, 2, _alta("r8"), ch("relationships", "r1", "delete")) == ["r2", "r3"]


def test_a4_con_tope_holgado_trae_toda_la_version(rels_db):
    assert _version_ids(rels_db, 5, _alta("r8"), ch("relationships", "r1", "delete")) == ["r2", "r3", "r8"]


@pytest.mark.parametrize("seed", range(40))
def test_a4_equivale_a_superponer_todo_y_cortar(monkeypatch, seed):
    """Fuzz de equivalencia: la lectura con ventana (`limit` + bajas) da lo
    mismo que superponer TODA la producción, ordenar por id y cortar — con
    ediciones, altas, revividas y bajas de ids que producción no tiene."""
    import random

    from app.features.reporting.draft import overlay_project

    rnd = random.Random(seed)
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    fake.raw["changesets"].insert_one({"_id": "cs1", "title": "v2", "owner": "ana", "projectId": P,
                                       "status": "draft"})
    pub = [_rel(f"r{n:03d}", "t1", "t2") for n in rnd.sample(range(100), rnd.randint(0, 20))]
    dead = [{**_rel(f"r{n:03d}", "t1", "t2"), "flgactive": False} for n in rnd.sample(range(100, 110), 2)]
    fake.raw["relationships"].insert_many(pub + dead)
    ids = [r["_id"] for r in pub]
    changes = [ch("relationships", i, "delete") if rnd.random() < 0.5 else _alta(i)
               for i in rnd.sample(ids, min(len(ids), rnd.randint(1, 6)))]
    changes += [_alta(f"r{n:03d}") for n in rnd.sample(range(200, 300), rnd.randint(0, 5))]
    changes += [ch("relationships", f"r{n:03d}", "delete") for n in rnd.sample(range(300, 310), rnd.randint(0, 2))]
    changes.append(_alta(dead[0]["_id"]))                                   # revive
    limit = rnd.randint(1, 25)
    got = _version_ids(fake, limit, *changes)
    everything = [{**{k: v for k, v in r.items() if k != "_id"}, "id": r["_id"]} for r in pub]
    ledger = asyncio.run(cs_repo.changes_map("cs1", ["relationships"]))["relationships"]
    assert got == sorted(d["id"] for d in overlay_project(everything, ledger, P))[:limit]


# ── A7 ──────────────────────────────────────────────────────────────────────
ANA = {"X-Dev-User": "ana"}
_VERSION_ROUTES = [
    ("GET", "/api/reporting/tables", None),
    ("GET", "/api/reporting/tables/count", None),
    ("GET", "/api/reporting/filters", None),
    ("GET", "/api/reporting/columns", {"tableId": "t1"}),
    ("GET", "/api/reporting/views", None),
    ("GET", "/api/reporting/insights/relationships", None),
    ("POST", "/api/reporting/columns/query", {"tableIds": ["t1"]}),
    ("POST", "/api/reporting/views/query", {"tableIds": ["t1"]}),
]


@pytest.mark.parametrize("method, path, extra", _VERSION_ROUTES)
def test_a7_una_sola_lectura_de_la_cabecera_por_request(report_version_db, client, monkeypatch,
                                                        method, path, extra):
    reads: list[str] = []
    real = cs_repo.get

    async def spy(cs_id):
        reads.append(cs_id)
        return await real(cs_id)
    monkeypatch.setattr(cs_repo, "get", spy)
    scope = {"projectId": P, "changesetId": "cs1", **(extra or {})}
    res = (client.get(path, params=scope, headers=ANA) if method == "GET"
           else client.post(path, json=scope, headers=ANA))
    assert res.status_code == 200, res.text[:200]
    assert reads == ["cs1"]


@pytest.mark.parametrize("user, perms, code", [
    ("ana", None, 200),                                   # dueña
    ("rev", {}, 200),                                     # revisor asignado
    ("admin", {"admin.manage": True}, 200),               # administrador
    ("jefe", {"versions.view_all": True}, 200),
    ("luis", {"model.edit": True}, 403),                  # ajeno
])
def test_a7_la_visibilidad_sigue_la_regla_del_canvas(report_version_db, client, monkeypatch, user, perms, code):
    """Guarda del arreglo: la regla es la misma `can_view` del canvas."""
    from unittest.mock import AsyncMock

    from app.features.auth import service as auth_service
    report_version_db.raw["changesets"].update_one({"_id": "cs1"},
                                                   {"$set": {"status": "submitted", "reviewers": ["rev"]}})
    monkeypatch.setattr(auth_service, "resolve_session_user",
                        AsyncMock(return_value=None if perms is None else {"permissions": perms}))
    res = client.get("/api/reporting/tables/count", params={"projectId": P, "changesetId": "cs1"},
                     headers={"X-Dev-User": user})
    assert res.status_code == code, res.text[:200]


# ── A3-o1: relaciones por LOTE de tablas (hoja Relationships del export) ────
def _lot_rows(lot: list[str], cs: str | None = None) -> list[tuple]:
    rows = asyncio.run(rep_views.relationships_lot_report(P, lot, cs))
    return [(r["id"], r["pairIndex"], r["parent"], r["child"]) for r in rows]


def test_a3o1_lote_en_produccion_trae_las_que_tocan_el_lote(report_version_db):
    assert _lot_rows(["t2"]) == [("r1", 0, "CLIENTE.COD", "CUENTA.CTA")]      # hijo en el lote
    assert _lot_rows(["t1"]) == [("r1", 0, "CLIENTE.COD", "CUENTA.CTA")]      # padre en el lote
    assert _lot_rows(["t3"]) == []


def test_a3o1_lote_con_la_version_suma_altas_y_nombres_de_la_version(report_version_db):
    assert _lot_rows(["t4"], "cs1") == [("r2", 0, "NUEVA.ID", "CLIENTE.COD")]
    assert _lot_rows(["t1"], "cs1") == [("r1", 0, "CLIENTE.COD", "CUENTA_NUEVA.CTA"),
                                        ("r2", 0, "NUEVA.ID", "CLIENTE.COD")]


def test_a3o1_la_version_saca_bajas_y_mudanzas_fuera_del_lote(report_version_db):
    report_version_db.raw["changeset_changes"].insert_one(
        ch("relationships", "r1", "upsert", {"parentTableId": "t4", "childTableId": "t5",
                                             "pairs": [{"parentColumnId": "c4", "childColumnId": "c4"}]}))
    assert [r[0] for r in _lot_rows(["t2"], "cs1")] == []                    # r1 se mudó fuera del lote
    assert [r[0] for r in _lot_rows(["t5"], "cs1")] == ["r1"]                # …y entra por el lote nuevo


@pytest.mark.parametrize("seed", range(40))
def test_a3o1_equivale_al_reporte_completo_filtrado_por_el_lote(monkeypatch, seed):
    """Fuzz de equivalencia: el lote da las MISMAS filas que el reporte completo
    (camino GET, tope holgado) filtrado a las relaciones que tocan el lote."""
    import random

    rnd = random.Random(1000 + seed)
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    fake.raw["changesets"].insert_one({"_id": "cs1", "title": "v2", "owner": "ana", "projectId": P,
                                       "status": "draft"})
    tables = [f"t{i}" for i in range(8)]
    pub = [_rel(f"r{n:02d}", *rnd.sample(tables, 2)) for n in rnd.sample(range(60), rnd.randint(0, 15))]
    if rnd.random() < 0.5 and pub:                                         # una en formato legacy
        legacy = pub.pop()
        pub.append({"_id": legacy["_id"], "projectId": P, "flgactive": True,
                    "sourceTableId": legacy["childTableId"], "sourceColumnId": "c2",
                    "targetTableId": legacy["parentTableId"], "targetColumnId": "c1"})
    if pub:
        fake.raw["relationships"].insert_many(pub)
    changes: list[dict] = []
    if rnd.random() < 0.8:
        for r in rnd.sample(pub, min(len(pub), rnd.randint(0, 5))):
            if rnd.random() < 0.5:
                changes.append(ch("relationships", r["_id"], "delete"))
            else:
                parent, child = rnd.sample(tables, 2)
                changes.append(ch("relationships", r["_id"], "upsert", {
                    "parentTableId": parent, "childTableId": child,
                    "pairs": [{"parentColumnId": "c1", "childColumnId": "c2"}]}))
        for n in rnd.sample(range(60, 90), rnd.randint(0, 4)):
            parent, child = rnd.sample(tables, 2)
            changes.append(ch("relationships", f"r{n:02d}", "upsert", {
                "parentTableId": parent, "childTableId": child,
                "pairs": [{"parentColumnId": "c1", "childColumnId": "c2"}]}))
    if changes:
        fake.raw["changeset_changes"].insert_many(changes)
    cs = "cs1" if rnd.random() < 0.8 else None
    ch_map = asyncio.run(cs_repo.changes_map(cs, ["relationships", "canonical_tables", "canonical_columns"])) if cs else None
    lot = rnd.sample(tables, rnd.randint(1, 4))
    full = asyncio.run(rep_views.relationships_report(P, limit=10_000, changes=ch_map))
    expected = sorted((r for r in full if r["parentTableId"] in lot or r["childTableId"] in lot),
                      key=lambda r: (r["id"], r["pairIndex"]))
    got = asyncio.run(rep_views.relationships_lot_report(P, lot, cs))
    assert sorted(got, key=lambda r: (r["id"], r["pairIndex"])) == expected


# ── A7 (segunda mitad): /tables en modo versión no lee los payloads de columnas ──
def test_a7_tables_lee_de_las_columnas_solo_op_y_tabla(report_version_db, client, monkeypatch):
    """Los conteos de columnas (`adjust_column_counts`) sólo necesitan `op` y
    `payload.tableId`: con una carga Excel grande el ledger de columnas es la
    lectura dominante — se trae proyectado, no completo."""
    from tests.support.fakedb import FakeCollection
    reads: list[tuple] = []
    real = FakeCollection.find

    def spy(self, flt=None, projection=None):
        if self.name == "changeset_changes" and "canonical_columns" in str(flt):
            reads.append((flt, projection))
        return real(self, flt, projection)

    monkeypatch.setattr(FakeCollection, "find", spy)
    res = client.get("/api/reporting/tables", params={"projectId": P, "changesetId": "cs1"}, headers=ANA)
    assert res.status_code == 200, res.text[:200]
    assert reads and all(proj and "payload.tableId" in proj and "payload" not in proj for _, proj in reads), reads
