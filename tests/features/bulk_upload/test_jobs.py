"""Registro en memoria de jobs de carga (doc 55 §3.2): vista pública, TTL,
tope por usuario, lock por changeset y cancelación al descartar."""
from __future__ import annotations

import asyncio

import pytest

from app.features.bulk_upload.jobs import (
    JOB_MAX_AGE_SECONDS, JOB_TTL_SECONDS, MAX_JOBS_PER_OWNER, JobRegistry, TooManyJobsError,
)
from app.features.bulk_upload.schemas import UploadSheets, UploadWorkbookBody

BODY = UploadWorkbookBody(fileName="f.xlsx", sheets=UploadSheets())


class _Clock:
    def __init__(self, t=1_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_create_get_y_vista_publica():
    clock = _Clock()
    reg = JobRegistry(clock=clock)
    job = reg.create("c1", "ana", "f.xlsx", BODY)
    assert reg.get(job.id) is job
    v = job.view()
    assert v["id"] == job.id and v["status"] == "validating" and v["fileName"] == "f.xlsx"
    assert v["progress"] == {"phase": "Queued", "done": 0, "total": 0}
    assert v["report"] is None and v["result"] is None and v["error"] is None
    assert v["createdAt"].startswith("1970-01-01T00:16:40")
    job.set_progress("Loading", 2, 5)
    assert job.view()["progress"] == {"phase": "Loading", "done": 2, "total": 5}


def test_evict_saca_terminados_viejos_y_colgados():
    clock = _Clock()
    reg = JobRegistry(clock=clock)
    done = reg.create("c1", "ana", "a", BODY)
    done.finish("applied")
    stuck = reg.create("c1", "ana", "b", BODY)          # se queda en validating
    fresh = reg.create("c1", "ana", "c", BODY)
    clock.t += JOB_TTL_SECONDS + 1
    assert reg.evict() == 1 and reg.get(done.id) is None and reg.get(stuck.id) is not None
    clock.t += JOB_MAX_AGE_SECONDS
    reg.evict()
    assert reg.get(stuck.id) is None and reg.get(fresh.id) is None


def test_tope_por_usuario_descarta_terminados_y_luego_falla():
    reg = JobRegistry(clock=_Clock())
    jobs = [reg.create("c1", "ana", str(i), BODY) for i in range(MAX_JOBS_PER_OWNER)]
    jobs[0].finish("validated")
    newer = reg.create("c1", "ana", "x", BODY)          # expulsa al terminado más viejo
    assert reg.get(jobs[0].id) is None and reg.get(newer.id) is not None
    with pytest.raises(TooManyJobsError):
        reg.create("c1", "ana", "y", BODY)               # todos activos
    assert reg.create("c1", "beto", "z", BODY) is not None   # otro usuario no se ve afectado


def test_lock_por_changeset_es_el_mismo_objeto():
    reg = JobRegistry(clock=_Clock())
    assert reg.lock_for("c1") is reg.lock_for("c1")
    assert reg.lock_for("c1") is not reg.lock_for("c2")


def test_discard_cancela_la_task_en_vuelo():
    reg = JobRegistry(clock=_Clock())

    async def run():
        job = reg.create("c1", "ana", "a", BODY)
        job.task = asyncio.create_task(asyncio.sleep(60))
        await asyncio.sleep(0)
        assert reg.discard(job.id) is job
        await asyncio.sleep(0)
        assert job.task.cancelled() and reg.get(job.id) is None
        assert reg.discard("nope") is None

    asyncio.run(run())
