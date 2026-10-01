"""Carreras del JobStore entre dos procesos (doc 105, revisión R1): cada test
intercala «el otro proceso» exactamente en la ventana leer → escribir. Cada
`JobStore` es un worker: sólo la BD es común."""
from __future__ import annotations

import asyncio

import pytest

from app.core.db import client as db_client
from app.features.bulk_upload.jobs import BODIES, JOB_STALE_SECONDS, JOB_TTL_SECONDS, JOBS, MAX_JOBS_PER_OWNER, JobStore
from app.features.bulk_upload.schemas import RawRow, RawSheet, UploadWorkbookBody
from tests.support.fakedb import FakeCollection, FakeDb

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


def _before_jobs_delete(monkeypatch, hook):
    """Corre `hook` (el otro proceso) justo antes del PRIMER borrado en `upload_jobs`."""
    real = FakeCollection.delete_many
    done = {"x": False}

    async def interleaved(self, flt, *a, **kw):
        if self.name == JOBS and not done["x"]:
            done["x"] = True
            await hook()
        return await real(self, flt, *a, **kw)

    monkeypatch.setattr(FakeCollection, "delete_many", interleaved)


def test_el_desalojo_no_borra_un_job_que_otro_proceso_reclamo_para_aplicar(db, monkeypatch):
    """Validó, volvió a los 31 min y pulsó Upload (proceso B) justo cuando otra
    carga (proceso A) desalojaba: el desalojo borraba por ids LEÍDOS y se
    llevaba el job ya `applying` con su cuerpo (el apply: «Upload not found»)."""
    clock = _Clock()
    a, b = JobStore(clock=clock), JobStore(clock=clock)

    async def go():
        job = await b.create("c1", "ana", "f.xlsx", BODY)
        await b.finish(job["_id"], "validated", report={"errorCount": 0})
        clock.t += JOB_TTL_SECONDS + 60

        async def claim():
            assert await b.claim_apply(job["_id"]) is not None

        _before_jobs_delete(monkeypatch, claim)
        await a.create("c2", "otro", "g.xlsx", BODY)          # dispara el desalojo en A
        left = await b.get(job["_id"])
        assert left is not None and left["status"] == "applying"
        assert await b.load_body(job["_id"]) is not None

    asyncio.run(go())


def test_en_un_mismo_desalojo_se_va_el_vencido_y_el_reclamado_conserva_su_cuerpo(db, monkeypatch):
    clock = _Clock()
    a, b = JobStore(clock=clock), JobStore(clock=clock)

    async def go():
        kept = await b.create("c1", "ana", "f.xlsx", BODY)
        gone = await b.create("c2", "ana", "g.xlsx", BODY)
        for j in (kept, gone):
            await b.finish(j["_id"], "validated", report={"errorCount": 0})
        clock.t += JOB_TTL_SECONDS + 60

        async def claim():
            assert await b.claim_apply(kept["_id"]) is not None

        _before_jobs_delete(monkeypatch, claim)
        await a.create("c3", "otro", "h.xlsx", BODY)
        assert await b.get(gone["_id"]) is None and await b.load_body(gone["_id"]) is None
        assert (await b.get(kept["_id"]))["status"] == "applying"
        assert await b.load_body(kept["_id"]) is not None

    asyncio.run(go())


def test_el_desalojo_si_borra_lo_vencido_y_sus_cuerpos(db):
    clock = _Clock()
    a = JobStore(clock=clock)

    async def go():
        old = await a.create("c1", "ana", "f.xlsx", BODY)
        await a.finish(old["_id"], "validated", report={"errorCount": 0})
        clock.t += JOB_TTL_SECONDS + 60
        await a.create("c2", "otro", "g.xlsx", BODY)
        assert await a.get(old["_id"]) is None
        assert await a.load_body(old["_id"]) is None

    asyncio.run(go())


def test_el_tope_por_usuario_no_borra_un_job_que_otro_proceso_reclamo(db, monkeypatch):
    clock = _Clock()
    a, b = JobStore(clock=clock), JobStore(clock=clock)

    async def go():
        ids = []
        for i in range(MAX_JOBS_PER_OWNER):
            job = await b.create(f"c{i}", "ana", "f.xlsx", BODY)
            await b.finish(job["_id"], "validated", report={"errorCount": 0})
            ids.append(job["_id"])
            clock.t += 1
        oldest = ids[0]

        async def claim():
            assert await b.claim_apply(oldest) is not None

        _before_jobs_delete(monkeypatch, claim)
        await a.create("cX", "ana", "f.xlsx", BODY)
        left = await b.get(oldest)
        assert left is not None and left["status"] == "applying"
        assert await b.load_body(oldest) is not None

    asyncio.run(go())


def test_descartar_una_validacion_que_termina_en_el_medio_la_borra(db, monkeypatch):
    """A descarta mientras B termina de validar entre la lectura y el borrado:
    antes respondía False (404 «Upload not found») y el job seguía vivo."""
    clock = _Clock()
    a, b = JobStore(clock=clock), JobStore(clock=clock)

    async def go():
        job = await b.create("c1", "ana", "f.xlsx", BODY)

        async def validated():
            await b.finish(job["_id"], "validated", report={"errorCount": 0})

        _before_jobs_delete(monkeypatch, validated)
        assert await a.discard(job["_id"]) is True
        assert await a.get(job["_id"]) is None
        assert await a.load_body(job["_id"]) is None

    asyncio.run(go())


def test_descartar_mientras_otro_proceso_empieza_a_aplicar_responde_ocupado(db, monkeypatch):
    clock = _Clock()
    a, b = JobStore(clock=clock), JobStore(clock=clock)

    async def go():
        job = await b.create("c1", "ana", "f.xlsx", BODY)
        await b.finish(job["_id"], "validated", report={"errorCount": 0})

        async def claim():
            assert await b.claim_apply(job["_id"]) is not None

        _before_jobs_delete(monkeypatch, claim)
        assert await a.discard(job["_id"]) == "busy"
        assert (await a.get(job["_id"]))["status"] == "applying"
        assert await a.load_body(job["_id"]) is not None

    asyncio.run(go())


@pytest.mark.parametrize("failing", [JOBS, BODIES])
def test_crear_no_deja_un_cuerpo_ni_un_job_huerfano_si_una_escritura_falla(db, monkeypatch, failing):
    """El cuerpo se insertaba ANTES que el job: una caída en el medio dejaba un
    cuerpo que ningún desalojo encontraba."""
    real = FakeCollection.insert_one

    async def insert_one(self, doc, *a, **kw):
        if self.name == failing:
            raise RuntimeError("db down")
        return await real(self, doc, *a, **kw)

    monkeypatch.setattr(FakeCollection, "insert_one", insert_one)
    store = JobStore(clock=_Clock())
    with pytest.raises(RuntimeError):
        asyncio.run(store.create("c1", "ana", "f.xlsx", BODY))
    assert db.raw[JOBS].count_documents({}) == 0
    assert db.raw[BODIES].count_documents({}) == 0


def test_un_cuerpo_cuyo_borrado_fallo_lo_barre_un_desalojo_posterior(db, monkeypatch):
    """Ronda 3 (revisor R4): el job se borraba y DESPUÉS su cuerpo; si lo
    segundo fallaba, el cuerpo (las hojas crudas del Excel) quedaba huérfano
    para siempre (el desalojo parte de `upload_jobs`). Y la falla tumbaba el
    POST de OTRO usuario, por limpiar lo ajeno."""
    clock = _Clock()
    store = JobStore(clock=clock)
    real = FakeCollection.delete_many
    state = {"fail": True}

    async def flaky(self, flt, *a, **kw):
        if self.name == BODIES and state["fail"]:
            state["fail"] = False
            raise TimeoutError("timeout borrando cuerpos")
        return await real(self, flt, *a, **kw)

    async def go():
        old = await store.create("c1", "ana", "f.xlsx", BODY)
        await store.finish(old["_id"], "validated", report={"errorCount": 0})
        clock.t += JOB_TTL_SECONDS + 60
        monkeypatch.setattr(FakeCollection, "delete_many", flaky)
        await store.create("c2", "beto", "g.xlsx", BODY)                  # el POST ajeno NO falla
        monkeypatch.setattr(FakeCollection, "delete_many", real)
        clock.t += JOB_TTL_SECONDS + JOB_STALE_SECONDS + 60
        await store.create("c3", "carla", "h.xlsx", BODY)                 # un desalojo posterior lo barre
        return old["_id"]

    jid = asyncio.run(go())
    assert db.raw[JOBS].find_one({"_id": jid}) is None
    assert db.raw[BODIES].find_one({"_id": jid}) is None


def test_el_barrido_no_toca_el_cuerpo_de_un_job_vivo_viejo(db):
    """Un job `applying` que late conserva su cuerpo aunque su `createdAt` sea viejo."""
    clock = _Clock()
    store = JobStore(clock=clock)

    async def go():
        job = await store.create("c1", "ana", "f.xlsx", BODY)
        await store.finish(job["_id"], "validated", report={"errorCount": 0})
        assert await store.claim_apply(job["_id"]) is not None
        for _ in range(6):                                                    # > STALE + TTL en total
            clock.t += JOB_STALE_SECONDS - 60
            await store.progress(job["_id"], "Writing changes", 1, 9)         # late
        await store.create("c2", "beto", "g.xlsx", BODY)
        return job["_id"]

    jid = asyncio.run(go())
    assert db.raw[BODIES].find_one({"_id": jid}) is not None


def test_el_barrido_de_cuerpos_se_espacia_no_corre_en_cada_carga(db, monkeypatch):
    """Ronda 4 (revisor R8): el barrido lee los cuerpos (hojas crudas de varios
    MB) y corría en CADA «Validate»; ahora como mucho una vez por intervalo."""
    from app.features.bulk_upload import jobs as jobs_mod

    clock = _Clock()
    store = JobStore(clock=clock)
    scans = {"n": 0}
    real_find = FakeCollection.find

    def spy(self, flt=None, *a, **kw):
        if self.name == BODIES:
            scans["n"] += 1
        return real_find(self, flt, *a, **kw)

    monkeypatch.setattr(FakeCollection, "find", spy)

    async def go():
        for _ in range(3):
            await store.create("c1", "ana", "f.xlsx", BODY)
            clock.t += 60
        n_three = scans["n"]
        clock.t += jobs_mod.SWEEP_EVERY_SECONDS
        await store.create("c1", "ana", "f.xlsx", BODY)
        return n_three, scans["n"]

    n_three, n_after = asyncio.run(go())
    assert n_three == 1 and n_after == 2, (n_three, n_after)
