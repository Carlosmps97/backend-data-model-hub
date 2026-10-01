"""Doc 105 (revisión de los arreglos, hallazgo 1 — costo «draft × lotes»).

`POST /api/reporting/insights/relationships/query` con `changesetId` leía, en
CADA lote (≤300 tablas), el ledger COMPLETO de relaciones, tablas y columnas
del draft (`version_changes(..., RELATIONSHIP_INPUTS)`: payloads + validación
Pydantic por documento), aunque sólo usa los nombres de las tablas y columnas
de SUS pares. Con un draft de carga Excel (cientos de miles de columnas) y 50
lotes era lo mismo que el A7 evitó en /tables.

Ahora cada lote lee del ledger SÓLO lo que lo toca: los cambios de las
relaciones que leyó (por su `_id` determinista), las altas y mudanzas con un
extremo — v2 o legacy — en el lote (por `payload`), y los de las tablas y
columnas de sus pares (por `_id`). Las filas deben ser IDÉNTICAS a las de antes:
fuzz de equivalencia contra el camino completo (ledger entero)."""
from __future__ import annotations

import asyncio
import random

import pytest

from app.core.db import client as db_client
from app.core.db.client import get_db
from app.core.scope import scoped
from app.features.changesets import repository as cs_repo
from app.features.relationships.models import RelationshipDoc
from app.features.reporting import versions
from app.features.reporting import views as rep_views
from app.features.reporting.draft import REL_ENDS, changes_of, overlay_named, overlay_relationships
from tests.support.fakedb import FakeCollection, FakeDb

from .helpers import AT, P, ch

ANA = {"X-Dev-User": "ana"}
REL_LOT = "/api/reporting/insights/relationships/query"


# ── Costo: cada lote lee SÓLO su tajada del ledger ───────────────────────────
def test_cada_lote_lee_solo_los_cambios_que_lo_tocan(report_version_db, client, monkeypatch):
    """Base: repro del revisor R2 (`test_a3o1_cada_lote_relee_todo_el_ledger_de_columnas`),
    con ruido también en relaciones y tablas ajenas a los lotes."""
    noise = [ch("canonical_columns", f"cx{i}", "upsert",
                {"tableId": "t9", "physicalName": f"C{i}", "logicalName": f"C{i}", "dataType": "INT", "ordinal": i})
             for i in range(2000)]
    noise += [ch("relationships", f"rx{i}", "upsert",
                 {"parentTableId": "t8", "childTableId": "t9",
                  "pairs": [{"parentColumnId": f"cx{i}", "childColumnId": f"cx{i + 1}"}]})
              for i in range(300)]
    noise += [ch("canonical_tables", f"tx{i}", "upsert", {"physicalName": f"TX{i}", "schema": "otro"})
              for i in range(300)]
    report_version_db.raw["changeset_changes"].insert_many(noise)
    read: list[int] = []
    real_find = FakeCollection.find

    def spy(self, flt=None, projection=None):
        if self.name == "changeset_changes":
            read.append(len(list(self._c.find(flt or {}, projection))))
        return real_find(self, flt, projection)

    monkeypatch.setattr(FakeCollection, "find", spy)
    rows = {}
    for lot in (["t1"], ["t2"], ["t3"]):
        res = client.post(REL_LOT, json={"projectId": P, "tableIds": lot, "changesetId": "cs1"}, headers=ANA)
        assert res.status_code == 200, res.text[:200]
        rows[lot[0]] = [(r["id"], r["parent"], r["child"]) for r in res.json()["data"]]
    # Lo que tocan los lotes: r2 (alta con hijo t1), t2/t4 (renombre/alta) y c4 (alta).
    assert sum(read) <= 6, read
    assert rows == {"t1": [("r1", "CLIENTE.COD", "CUENTA_NUEVA.CTA"), ("r2", "NUEVA.ID", "CLIENTE.COD")],
                    "t2": [("r1", "CLIENTE.COD", "CUENTA_NUEVA.CTA")],
                    "t3": []}


# ── Equivalencia con el camino completo (el de antes del arreglo) ────────────
async def _camino_completo(lot: list[str], cs: str | None) -> list[dict]:
    """Oráculo: la lectura de ANTES del arreglo — el ledger COMPLETO de la
    versión (`changes_map` con `RELATIONSHIP_INPUTS`) superpuesto a las
    relaciones publicadas del lote, y los nombres de tablas y columnas de ese
    mismo mapa. El formato de fila es el de producción (`_rows`)."""
    full = await cs_repo.changes_map(cs, versions.RELATIONSHIP_INPUTS) if cs else None
    db = await get_db()
    query = scoped(P, {**rep_views.ACTIVE, "$or": [{k: {"$in": lot}} for k in REL_ENDS]})
    docs = rep_views._rel_docs(await db["relationships"].find(query).to_list(None))
    rel_changes = changes_of(full, "relationships")
    if rel_changes:
        docs = overlay_relationships(docs, rel_changes, P, set(lot))
    docs = sorted(docs, key=lambda d: d["id"])
    rels = [RelationshipDoc.model_validate(d).model_dump() for d in docs]
    tids = {t for r in rels for t in (r["parentTableId"], r["childTableId"]) if t}
    cids = {c for r in rels for p in r["pairs"] for c in (p.get("parentColumnId"), p.get("childColumnId")) if c}
    tmap = {str(t["_id"]): t async for t in db["canonical_tables"].find({"_id": {"$in": list(tids)}},
                                                                          {"physicalName": 1, "schema": 1})}
    cmap = {str(c["_id"]): c.get("physicalName")
            async for c in db["canonical_columns"].find({"_id": {"$in": list(cids)}}, {"physicalName": 1})}
    if full:
        tmap = overlay_named(tmap, changes_of(full, "canonical_tables"), tids)
        named = overlay_named({k: {"physicalName": v} for k, v in cmap.items()},
                              changes_of(full, "canonical_columns"), cids)
        cmap = {k: d.get("physicalName") for k, d in named.items()}
    return rep_views._rows(rels, tmap, cmap)


TABLES = [f"t{i}" for i in range(8)]          # t7 inactiva; t8/t9 sólo existen en la versión


def _cols(t: str) -> list[str]:
    return [f"c{t}_{k}" for k in range(3)]


def _pairs(rnd: random.Random, parent: str, child: str) -> list[dict]:
    return [{"parentColumnId": rnd.choice(_cols(parent)), "childColumnId": rnd.choice(_cols(child)),
             **({"roleName": "ROL"} if rnd.random() < 0.2 else {})}
            for _ in range(rnd.randint(1, 3))]


def _v2(rnd: random.Random, parent: str, child: str) -> dict:
    return {"parentTableId": parent, "childTableId": child, "pairs": _pairs(rnd, parent, child),
            "identifying": rnd.random() < 0.3,
            "parentCardinality": rnd.choice(["one", "zero-one"]),
            "childCardinality": rnd.choice(["zero-many", "one-many", "many"]),
            **({"parentToChildPhrase": "tiene"} if rnd.random() < 0.3 else {})}


def _legacy(rnd: random.Random, parent: str, child: str) -> dict:
    """Forma v1 (pre-doc 19): source = hijo, target = padre, un solo par."""
    return {"sourceTableId": child, "sourceColumnId": rnd.choice(_cols(child)),
            "targetTableId": parent, "targetColumnId": rnd.choice(_cols(parent)),
            "sourceCardinality": "many", "targetCardinality": "one"}


def _ends(rnd: random.Random, universe: list[str]) -> tuple[str, str]:
    parent, child = rnd.choice(universe), rnd.choice(universe)     # puede ser auto-referencial
    return parent, child


def _seed_world(rnd: random.Random, fake: FakeDb) -> None:
    raw = fake.raw
    raw["changesets"].insert_one({"_id": "cs1", "title": "v2", "owner": "ana", "projectId": P, "status": "draft"})
    raw["canonical_tables"].insert_many([
        {"_id": t, "projectId": P, "flgactive": t != "t7", "physicalName": f"T_{t.upper()}",
         "schema": rnd.choice(["core", "risk"])} for t in TABLES])
    raw["canonical_columns"].insert_many([
        {"_id": c, "projectId": P, "flgactive": rnd.random() < 0.9, "tableId": t, "physicalName": c.upper()}
        for t in TABLES for c in _cols(t)])
    rels: list[dict] = []
    for n in rnd.sample(range(100), rnd.randint(0, 25)):
        parent, child = _ends(rnd, TABLES)
        shape = _legacy(rnd, parent, child) if rnd.random() < 0.25 else _v2(rnd, parent, child)
        rels.append({"_id": f"r{n:03d}", "projectId": P if rnd.random() < 0.9 else "p2",
                     "flgactive": rnd.random() < 0.85, **shape})
    if rels:
        raw["relationships"].insert_many(rels)

    changes: list[dict] = []
    for r in rnd.sample(rels, min(len(rels), rnd.randint(0, 8))):
        roll = rnd.random()
        if roll < 0.35:
            changes.append(ch("relationships", r["_id"], "delete"))
        else:                                    # edición / mudanza / revive (si estaba inactiva)
            parent, child = _ends(rnd, TABLES + ["t8", "t9"])
            shape = _legacy(rnd, parent, child) if roll > 0.9 else _v2(rnd, parent, child)
            changes.append(ch("relationships", r["_id"], "upsert", shape))
    for n in rnd.sample(range(100, 200), rnd.randint(0, 6)):       # altas
        parent, child = _ends(rnd, TABLES + ["t8", "t9"])
        shape = _legacy(rnd, parent, child) if rnd.random() < 0.25 else _v2(rnd, parent, child)
        roll = rnd.random()
        if roll < 0.15:                                             # de OTRO proyecto
            changes.append(ch("relationships", f"r{n:03d}", "upsert", {**shape, "projectId": "p2"}))
        elif roll < 0.25:                                           # payload sin projectId
            changes.append({"_id": f"cs1::relationships::r{n:03d}", "csId": "cs1", "collection": "relationships",
                            "entityId": f"r{n:03d}", "op": "upsert", "at": AT, "payload": shape})
        else:
            changes.append(ch("relationships", f"r{n:03d}", "upsert", shape))
    for n in rnd.sample(range(200, 300), rnd.randint(0, 2)):       # bajas de ids desconocidos
        changes.append(ch("relationships", f"r{n:03d}", "delete"))
    for t in rnd.sample(TABLES, rnd.randint(0, 4)):                 # tablas: renombres y bajas
        changes.append(ch("canonical_tables", t, "delete") if rnd.random() < 0.3 else
                       ch("canonical_tables", t, "upsert", {"physicalName": f"{t.upper()}_V2",
                                                            "schema": rnd.choice(["core", "nuevo"])}))
    for t in ("t8", "t9"):                                          # tablas nuevas de la versión
        if rnd.random() < 0.6:
            changes.append(ch("canonical_tables", t, "upsert", {"physicalName": f"NUEVA_{t.upper()}",
                                                                "schema": "nuevo"}))
    all_cols = [c for t in TABLES + ["t8", "t9"] for c in _cols(t)]
    for c in rnd.sample(all_cols, rnd.randint(0, 10)):              # columnas: renombres, bajas, altas
        tid = c[1:].split("_")[0]
        changes.append(ch("canonical_columns", c, "delete") if rnd.random() < 0.3 else
                       ch("canonical_columns", c, "upsert", {"tableId": tid, "physicalName": f"{c.upper()}_V2"}))
    changes += [ch("canonical_columns", f"ruido{i}", "upsert", {"tableId": "tq", "physicalName": f"R{i}"})
                for i in range(rnd.randint(0, 30))]                 # ruido fuera de todo lote
    if changes:
        raw["changeset_changes"].insert_many(list({d["_id"]: d for d in changes}.values()))


@pytest.mark.parametrize("seed", range(60))
def test_el_lote_da_las_mismas_filas_que_el_camino_completo(monkeypatch, seed):
    rnd = random.Random(10_500 + seed)
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    _seed_world(rnd, fake)
    for _ in range(4):
        lot = rnd.sample(TABLES + ["t8", "t9", "tz"], rnd.randint(1, 4))
        cs = "cs1" if rnd.random() < 0.85 else None
        got = asyncio.run(rep_views.relationships_lot_report(P, lot, cs))
        assert got == asyncio.run(_camino_completo(lot, cs)), (seed, lot, cs)
