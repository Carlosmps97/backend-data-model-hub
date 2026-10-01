"""Jobs de carga masiva EN LA BASE (doc 55 §3.2 · doc 105): vista pública,
avance, TTL, tope por usuario, claim atómico del apply, descarte — y lo que
motivó el cambio: `app.yaml` corre `--workers 2`, así que el polling, el apply
y el descarte pueden caer en OTRO proceso que el que creó el job. Cada
`JobStore` de estos tests es «un worker»: no comparten memoria, sólo la BD."""
from __future__ import annotations

import asyncio

import pytest

from app.core.db import client as db_client
from app.features.bulk_upload.jobs import (
    JOB_STALE_SECONDS, JOB_TTL_SECONDS, MAX_JOBS_PER_OWNER, JobStore, TooManyJobsError,
)
from app.features.bulk_upload.schemas import RawRow, RawSheet, UploadWorkbookBody
from tests.support.fakedb import FakeDb

BODY = UploadWorkbookBody(fileName="f.xlsx", profileId="pf",
                          sheets=[RawSheet(name="Tablas", rows=[RawRow(row=2, cells=["A", "B"])])])


class _Clock:
    def __init__(self, t=1_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def db(monkeypatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    return fake


def run(coro):
    return asyncio.run(coro)


def test_otro_worker_ve_el_job_su_avance_y_su_cuerpo(db):
    """El bug de `--workers 2`: el job vivía en la memoria de UN proceso y el
    polling que caía en el otro respondía 404 («validate again»)."""
    clock = _Clock()
    a, b = JobStore(clock=clock), JobStore(clock=clock)

    async def go():
        job = await a.create("c1", "ana", "f.xlsx", BODY)
        seen = await b.get(job["_id"])
        assert seen is not None and seen["csId"] == "c1" and seen["owner"] == "ana"
        await a.progress(job["_id"], "Loading", 2, 5)
        assert b.view(await b.get(job["_id"]))["progress"] == {"phase": "Loading", "done": 2, "total": 5}
        assert await b.load_body(job["_id"]) == BODY

    run(go())


def test_create_y_vista_publica(db):
    clock = _Clock()
    store = JobStore(clock=clock)

    async def go():
        job = await store.create("c1", "ana", "f.xlsx", BODY)
        v = store.view(await store.get(job["_id"]))
        assert v["id"] == job["_id"] and v["csId"] == "c1" and v["status"] == "validating"
        assert v["fileName"] == "f.xlsx" and v["progress"] == {"phase": "Queued", "done": 0, "total": 0}
        assert v["report"] is None and v["result"] is None and v["error"] is None
        assert v["createdAt"].startswith("1970-01-01T00:16:40")
        assert "body" not in v and "owner" not in v

    run(go())


def test_finish_guarda_reporte_y_resultado_y_suelta_el_cuerpo_si_ya_no_sirve(db):
    store = JobStore(clock=_Clock())

    async def go():
        v1 = await store.create("c1", "ana", "a", BODY)
        await store.finish(v1["_id"], "validated", report={"errorCount": 0})
        assert (await store.get(v1["_id"]))["report"] == {"errorCount": 0}
        assert await store.load_body(v1["_id"]) == BODY            # el apply lo necesita
        await store.finish(v1["_id"], "applied", result={"counts": {}})
        assert await store.load_body(v1["_id"]) is None           # ya no
        v2 = await store.create("c1", "ana", "b", BODY)
        await store.finish(v2["_id"], "failed", error="boom")
        assert store.view(await store.get(v2["_id"]))["error"] == "boom"
        assert await store.load_body(v2["_id"]) is None

    run(go())


def test_un_job_descartado_no_revive_con_la_escritura_tardia_de_su_task(db):
    store = JobStore(clock=_Clock())

    async def go():
        job = await store.create("c1", "ana", "a", BODY)
        assert await store.discard(job["_id"]) is True
        await store.progress(job["_id"], "Late", 1, 1)
        await store.finish(job["_id"], "validated", report={})
        assert await store.get(job["_id"]) is None and await store.load_body(job["_id"]) is None

    run(go())


def test_job_activo_sin_avance_se_ve_fallido(db):
    """Un proceso que murió (reinicio, OOM) deja su job «activo» para siempre:
    sin avance en JOB_STALE_SECONDS se informa como fallido, con qué hacer."""
    clock = _Clock()
    store = JobStore(clock=clock)

    async def go():
        job = await store.create("c1", "ana", "a", BODY)
        clock.t += JOB_STALE_SECONDS - 1
        assert store.view(await store.get(job["_id"]))["status"] == "validating"
        clock.t += 2
        v = store.view(await store.get(job["_id"]))
        assert v["status"] == "failed" and "stopped unexpectedly" in v["error"]

    run(go())


def test_claim_apply_es_atomico_y_solo_desde_validated(db):
    clock = _Clock()
    a, b = JobStore(clock=clock), JobStore(clock=clock)

    async def go():
        job = await a.create("c1", "ana", "a", BODY)
        assert await a.claim_apply(job["_id"]) is None           # todavía validando
        await a.finish(job["_id"], "validated", report={"errorCount": 0})
        first, second = await asyncio.gather(a.claim_apply(job["_id"]), b.claim_apply(job["_id"]))
        assert [x is not None for x in (first, second)].count(True) == 1
        assert (await a.get(job["_id"]))["status"] == "applying"
        await b.unclaim_apply(job["_id"])
        assert (await a.get(job["_id"]))["status"] == "validated"

    run(go())


def test_discard_no_corta_un_apply_vivo_pero_si_uno_muerto(db):
    clock = _Clock()
    store = JobStore(clock=clock)

    async def go():
        job = await store.create("c1", "ana", "a", BODY)
        await store.finish(job["_id"], "validated", report={"errorCount": 0})
        await store.claim_apply(job["_id"])
        assert await store.discard(job["_id"]) == "busy"
        clock.t += JOB_STALE_SECONDS + 1                          # su proceso murió
        assert await store.discard(job["_id"]) is True
        assert await store.discard(job["_id"]) is False

    run(go())


def test_discard_condicionado_solo_borra_en_el_estado_pedido_doc105(db):
    """Doc 105 (ronda 8): con el POST del apply sin respuesta, el front confirma
    «no llegó» descartando el job SÓLO si sigue «validated». Si entretanto se
    aplicó (o se está aplicando), no se borra — el front sigue su polling y ve
    el resultado real en vez de avisar «nada se aplicó»."""
    store = JobStore()

    async def go():
        job = await store.create("c1", "ana", "a", BODY)
        await store.finish(job["_id"], "validated", report={"errorCount": 0})
        await store.claim_apply(job["_id"])
        assert await store.discard(job["_id"], only_status="validated") == "changed"      # aplicándose
        await store.finish(job["_id"], "applied", result={"counts": {}})                  # terminó antes del DELETE
        assert await store.discard(job["_id"], only_status="validated") == "changed"
        assert (await store.get(job["_id"]))["status"] == "applied"                       # su resultado sigue ahí
        other = await store.create("c1", "ana", "b", BODY)
        await store.finish(other["_id"], "validated", report={"errorCount": 0})
        assert await store.discard(other["_id"], only_status="validated") is True
        assert await store.discard(other["_id"], only_status="validated") is False

    run(go())


def test_evict_saca_terminados_viejos_y_activos_muertos(db):
    clock = _Clock()
    store = JobStore(clock=clock)

    async def go():
        done = await store.create("c1", "ana", "a", BODY)
        await store.finish(done["_id"], "validated", report={})
        stuck = await store.create("c1", "ana", "b", BODY)          # su proceso murió validando
        clock.t += JOB_TTL_SECONDS + 1
        fresh = await store.create("c1", "ana", "c", BODY)          # crear también desaloja
        assert await store.get(done["_id"]) is None and await store.load_body(done["_id"]) is None
        assert await store.get(stuck["_id"]) is not None            # aún se informa «stopped unexpectedly»
        clock.t += JOB_STALE_SECONDS                                # muerto + TTL: ya se informó bastante
        assert await store.evict() == 1
        assert await store.get(stuck["_id"]) is None and await store.load_body(stuck["_id"]) is None
        assert await store.get(fresh["_id"]) is not None

    run(go())


def test_tope_por_usuario_descarta_terminados_y_luego_falla(db):
    store = JobStore(clock=_Clock())

    async def go():
        jobs = [await store.create("c1", "ana", str(i), BODY) for i in range(MAX_JOBS_PER_OWNER)]
        await store.finish(jobs[0]["_id"], "validated", report={})
        newer = await store.create("c1", "ana", "x", BODY)          # expulsa al terminado más viejo
        assert await store.get(jobs[0]["_id"]) is None and await store.get(newer["_id"]) is not None
        with pytest.raises(TooManyJobsError):
            await store.create("c1", "ana", "y", BODY)               # todos activos
        assert await store.create("c1", "beto", "z", BODY) is not None   # otro usuario no se ve afectado

    run(go())
