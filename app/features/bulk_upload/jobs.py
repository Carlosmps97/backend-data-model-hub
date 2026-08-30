"""Registro en memoria de los jobs de carga masiva (doc 55 §3.2).

Vive en el proceso (uvicorn corre UN worker en Databricks Apps): si la app
se reinicia, los jobs desaparecen y el front pide validar de nuevo (404).
Eviction por TTL (terminados) y por edad máxima (colgados); tope por usuario
para que un cliente que no descarta no acumule reportes; un `asyncio.Lock`
por changeset serializa los apply sobre la misma versión.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .schemas import UploadWorkbookBody

JOB_TTL_SECONDS = 30 * 60          # terminados: 30 min tras la última actualización
JOB_MAX_AGE_SECONDS = 60 * 60      # colgados (validating/applying sin terminar): 1 h desde su creación
MAX_JOBS_PER_OWNER = 20

TERMINAL = frozenset({"validated", "applied", "failed"})


class TooManyJobsError(RuntimeError):
    """El usuario tiene demasiados jobs activos; el router la convierte en 429."""


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


@dataclass
class UploadJob:
    id: str
    cs_id: str
    owner: str
    file_name: str
    body: UploadWorkbookBody
    created_at: float
    updated_at: float
    status: str = "validating"          # validating | validated | applying | applied | failed
    progress: dict = field(default_factory=lambda: {"phase": "Queued", "done": 0, "total": 0})
    report: dict | None = None
    result: dict | None = None
    error: str | None = None
    task: asyncio.Task | None = None
    _clock: Callable[[], float] = field(default=time.time, repr=False)

    def touch(self) -> None:
        self.updated_at = self._clock()

    def set_progress(self, phase: str, done: int, total: int) -> None:
        self.progress = {"phase": phase, "done": done, "total": total}
        self.touch()

    def finish(self, status: str, *, error: str | None = None) -> None:
        self.status = status
        self.error = error
        self.touch()

    @property
    def active(self) -> bool:
        return self.status not in TERMINAL

    def view(self) -> dict:
        """Forma pública (la que hace polling el front)."""
        return {
            "id": self.id, "csId": self.cs_id, "fileName": self.file_name, "status": self.status,
            "progress": dict(self.progress), "report": self.report, "result": self.result,
            "error": self.error, "createdAt": _iso(self.created_at), "updatedAt": _iso(self.updated_at),
        }


class JobRegistry:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._jobs: dict[str, UploadJob] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def create(self, cs_id: str, owner: str, file_name: str, body: UploadWorkbookBody) -> UploadJob:
        self.evict()
        mine = sorted((j for j in self._jobs.values() if j.owner == owner), key=lambda j: j.created_at)
        while len(mine) >= MAX_JOBS_PER_OWNER:
            victim = next((j for j in mine if not j.active), None)
            if victim is None:
                raise TooManyJobsError(f"Too many uploads in progress for {owner} (max {MAX_JOBS_PER_OWNER}).")
            self._jobs.pop(victim.id, None)
            mine.remove(victim)
        now = self._clock()
        job = UploadJob(id=uuid.uuid4().hex, cs_id=cs_id, owner=owner, file_name=file_name, body=body,
                        created_at=now, updated_at=now, _clock=self._clock)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> UploadJob | None:
        return self._jobs.get(job_id)

    def discard(self, job_id: str) -> UploadJob | None:
        job = self._jobs.pop(job_id, None)
        if job is not None and job.task is not None and not job.task.done():
            job.task.cancel()
        return job

    def lock_for(self, cs_id: str) -> asyncio.Lock:
        lock = self._locks.get(cs_id)
        if lock is None:
            lock = self._locks[cs_id] = asyncio.Lock()
        return lock

    def evict(self) -> int:
        now = self._clock()
        gone = [j.id for j in self._jobs.values()
                if (not j.active and now - j.updated_at > JOB_TTL_SECONDS)
                or (j.active and now - j.created_at > JOB_MAX_AGE_SECONDS)]
        for jid in gone:
            self.discard(jid)
        return len(gone)


registry = JobRegistry()
