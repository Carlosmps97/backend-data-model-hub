"""Jobs de la carga masiva (doc 55 §3.2) — viven en la BASE (doc 105).

`app.yaml` corre `uvicorn --workers 2`: el POST que crea el job, el polling,
el apply y el descarte pueden caer en procesos distintos. Con el registro en
memoria de antes, el polling que caía en el otro proceso respondía 404
(«validate again») y el lock por versión no cubría ambos. Acá sólo la TASK
que corre la validación o el apply es del proceso que la lanzó; su estado,
avance, reporte y cuerpo (las hojas crudas) están en la BD:

- `upload_jobs`: {_id, csId, owner, fileName, status, progress, report,
  result, error, createdAt, updatedAt} — timestamps en epoch (segundos).
- `upload_job_bodies`: {_id (= id del job), body, createdAt}: lo lee el apply
  (en cualquier proceso) para re-validar; se borra cuando ya no sirve.

Un job activo (validating/applying) sin avance en JOB_STALE_SECONDS es de un
proceso que murió: se informa como fallido y el descarte lo permite. El lock
de «un apply por versión» es la marca `uploadLock` de la cabecera del
changeset (`changesets.repository.claim_upload_lock`), no de este módulo.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from pymongo import ReturnDocument

from app.core.db.client import get_db
from app.core.logging import get_logger

from .schemas import UploadWorkbookBody

log = get_logger("app.bulk_upload.jobs")

JOBS = "upload_jobs"
BODIES = "upload_job_bodies"

JOB_TTL_SECONDS = 30 * 60          # terminados: 30 min tras la última actualización
SWEEP_EVERY_SECONDS = 30 * 60      # barrido de cuerpos huérfanos: como mucho uno por intervalo
JOB_STALE_SECONDS = 10 * 60        # activo sin avance en 10 min = su proceso murió
MAX_JOBS_PER_OWNER = 20

TERMINAL = frozenset({"validated", "applied", "failed"})
ACTIVE = ("validating", "applying")

STALE_ERROR = ("The upload stopped unexpectedly (the server restarted). Part of it may already be in the "
               "version: validate the workbook again to finish it.")


class TooManyJobsError(RuntimeError):
    """El usuario tiene demasiados jobs activos; el router la convierte en 429."""


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class JobStore:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._last_sweep = float("-inf")

    def now(self) -> float:
        return self._clock()

    def is_stale(self, job: dict) -> bool:
        """¿Activo pero sin avance en JOB_STALE_SECONDS (su proceso murió)?"""
        return job.get("status") in ACTIVE and self.now() - float(job.get("updatedAt") or 0) > JOB_STALE_SECONDS

    def view(self, job: dict) -> dict:
        """Forma pública (la que hace polling el front)."""
        status, error = job.get("status"), job.get("error")
        if self.is_stale(job):
            status, error = "failed", STALE_ERROR
        return {
            "id": job["_id"], "csId": job.get("csId"), "fileName": job.get("fileName"), "status": status,
            "progress": dict(job.get("progress") or {}), "report": job.get("report"),
            "result": job.get("result"), "error": error,
            "createdAt": _iso(float(job.get("createdAt") or 0)), "updatedAt": _iso(float(job.get("updatedAt") or 0)),
        }

    async def create(self, cs_id: str, owner: str, file_name: str, body: UploadWorkbookBody) -> dict:
        # Limpieza best-effort (ronda 3): una falla al desalojar jobs AJENOS no
        # tumba el POST de este usuario; el próximo desalojo lo reintenta.
        try:
            await self.evict()
        except Exception:  # noqa: BLE001
            log.warning("upload job eviction failed; retried on the next upload", exc_info=True)
        db = await get_db()
        mine = await db[JOBS].find({"owner": owner}, {"status": 1, "updatedAt": 1, "createdAt": 1}) \
            .sort("createdAt", 1).to_list(None)
        excess = len(mine) - MAX_JOBS_PER_OWNER + 1
        if excess > 0:
            done = [j["_id"] for j in mine if j.get("status") in TERMINAL or self.is_stale(j)]
            # Del más viejo al más nuevo, cada borrado re-chequea (doc 105, R1):
            # un job que otro proceso reclamó en el medio ya no es reemplazable.
            removed = await self._delete_where(done[:excess], self._replaceable())
            for jid in done[excess:]:
                if removed >= excess:
                    break
                removed += await self._delete_where([jid], self._replaceable())
            if removed < excess:
                raise TooManyJobsError(f"Too many uploads in progress for {owner} (max {MAX_JOBS_PER_OWNER}).")
        now = self.now()
        job = {"_id": uuid.uuid4().hex, "csId": cs_id, "owner": owner, "fileName": file_name,
               "status": "validating", "progress": {"phase": "Queued", "done": 0, "total": 0},
               "report": None, "result": None, "error": None, "createdAt": now, "updatedAt": now}
        # El job ANTES que su cuerpo (doc 105, R1): al revés, una caída en el
        # medio dejaba un cuerpo que ningún desalojo encontraba. Sin cuerpo el
        # job no sirve: se retira y el POST falla (si tampoco se puede, queda
        # `validating` sin avance → muerto → desalojado como cualquier otro).
        await db[JOBS].insert_one(job)
        try:
            await db[BODIES].insert_one({"_id": job["_id"], "body": body.model_dump(), "createdAt": now})
        except Exception:
            try:
                await db[JOBS].delete_many({"_id": job["_id"]})
            except Exception:  # noqa: BLE001
                pass
            raise
        return job

    async def get(self, job_id: str) -> dict | None:
        db = await get_db()
        return await db[JOBS].find_one({"_id": job_id})

    async def load_body(self, job_id: str) -> UploadWorkbookBody | None:
        db = await get_db()
        doc = await db[BODIES].find_one({"_id": job_id})
        return UploadWorkbookBody.model_validate(doc["body"]) if doc else None

    async def progress(self, job_id: str, phase: str, done: int, total: int) -> None:
        """Avance (y latido: un job que avanza no está muerto). Sin upsert: un
        job descartado no revive con la escritura tardía de su task."""
        db = await get_db()
        await db[JOBS].update_one({"_id": job_id}, {"$set": {
            "progress": {"phase": phase, "done": done, "total": total}, "updatedAt": self.now()}})

    async def finish(self, job_id: str, status: str, *, error: str | None = None,
                     report: dict | None = None, result: dict | None = None) -> None:
        fields: dict = {"status": status, "error": error, "updatedAt": self.now()}
        if report is not None:
            fields["report"] = report
        if result is not None:
            fields["result"] = result
        db = await get_db()
        await db[JOBS].update_one({"_id": job_id}, {"$set": fields})
        if status != "validated":                  # sólo un validado espera su apply
            await db[BODIES].delete_many({"_id": job_id})

    async def claim_apply(self, job_id: str) -> dict | None:
        """validated → applying en UNA escritura condicionada: dos clics (o dos
        procesos) sobre el mismo job, sólo uno lo toma. None = no estaba
        validado (sigue validando, falló, ya se aplica o se descartó)."""
        db = await get_db()
        return await db[JOBS].find_one_and_update(
            {"_id": job_id, "status": "validated"},
            {"$set": {"status": "applying", "progress": {"phase": "Queued", "done": 0, "total": 0},
                      "error": None, "updatedAt": self.now()}},
            return_document=ReturnDocument.AFTER)

    async def unclaim_apply(self, job_id: str) -> None:
        """Deshace `claim_apply` cuando la versión no dio su lock (otro apply
        en curso, o cambió de manos): el job vuelve a estar listo."""
        db = await get_db()
        await db[JOBS].update_one({"_id": job_id, "status": "applying"},
                                  {"$set": {"status": "validated", "updatedAt": self.now()}})

    async def discard(self, job_id: str, *, only_status: str | None = None) -> bool | str:
        """True si se borró; "busy" si está aplicando (cortarlo a mitad dejaría
        tandas escritas sin el resto — salvo que su proceso haya muerto);
        False si no existe. Doc 105 (ronda 8): con `only_status`, sólo se borra
        en ESE estado — si no, "changed" (p. ej. el POST del apply sin respuesta
        llegó y la carga ya empezó o terminó: su resultado no se pierde)."""
        db = await get_db()
        for _ in range(3):
            job = await db[JOBS].find_one({"_id": job_id})
            if job is None:
                return False
            if only_status is not None and job.get("status") != only_status:
                return "changed"
            if job.get("status") == "applying" and not self.is_stale(job):
                return "busy"
            # Condicionado a lo leído: un apply que lo tomó en el medio no se borra.
            flt: dict = {"_id": job_id, "status": job.get("status")}
            if job.get("status") == "applying":
                flt["updatedAt"] = job.get("updatedAt")
            res = await db[JOBS].delete_many(flt)
            if res.deleted_count:
                await db[BODIES].delete_many({"_id": job_id})
                return True
            # Cambió entre la lectura y el borrado (doc 105, R1: otro proceso
            # terminó de validarlo o lo reclamó): se decide con el estado nuevo.
        return "busy"

    def _evictable(self) -> dict:
        """Terminados sin tocar en JOB_TTL_SECONDS y activos muertos hace más
        de JOB_TTL_SECONDS (su «stopped unexpectedly» ya se pudo ver)."""
        now = self.now()
        return {"$or": [
            {"status": {"$in": sorted(TERMINAL)}, "updatedAt": {"$lt": now - JOB_TTL_SECONDS}},
            {"status": {"$in": list(ACTIVE)}, "updatedAt": {"$lt": now - JOB_STALE_SECONDS - JOB_TTL_SECONDS}},
        ]}

    def _replaceable(self) -> dict:
        """Lo que el tope por usuario puede sacar: terminado o muerto."""
        return {"$or": [
            {"status": {"$in": sorted(TERMINAL)}},
            {"status": {"$in": list(ACTIVE)}, "updatedAt": {"$lt": self.now() - JOB_STALE_SECONDS}},
        ]}

    async def evict(self) -> int:
        db = await get_db()
        gone = await db[JOBS].find(self._evictable(), {"status": 1}).to_list(None)
        n = await self._delete_where([j["_id"] for j in gone], self._evictable())
        await self._sweep_bodies()
        return n

    async def _sweep_bodies(self) -> None:
        """Cuerpos sin job y más viejos que cualquier job vivo (ronda 3): el
        job se borra ANTES que su cuerpo, y si lo segundo falló (o el proceso
        murió en el medio) el cuerpo quedaba huérfano para siempre. Ronda 4:
        leer los cuerpos (hojas crudas de varios MB) cuesta; los huérfanos son
        raros e inofensivos — como mucho un barrido por SWEEP_EVERY_SECONDS en
        cada proceso, no en cada «Validate»."""
        if self.now() - self._last_sweep < SWEEP_EVERY_SECONDS:
            return
        self._last_sweep = self.now()
        db = await get_db()
        cutoff = self.now() - JOB_STALE_SECONDS - JOB_TTL_SECONDS
        old = [d["_id"] for d in await db[BODIES].find({"createdAt": {"$lt": cutoff}}, {"_id": 1}).to_list(None)]
        if not old:
            return
        alive = {d["_id"] for d in await db[JOBS].find({"_id": {"$in": old}}, {"_id": 1}).to_list(None)}
        orphans = [i for i in old if i not in alive]
        if orphans:
            await db[BODIES].delete_many({"_id": {"$in": orphans}})

    async def _delete_where(self, ids: list[str], still: dict) -> int:
        """Borra de `ids` los que TODAVÍA cumplen `still`, en la misma sentencia
        (doc 105, R1): antes se borraba por ids leídos y un job que otro proceso
        reclamó en el medio (`applying`) se iba con su cuerpo. Los cuerpos se
        borran sólo de los jobs que ya no están."""
        if not ids:
            return 0
        db = await get_db()
        res = await db[JOBS].delete_many({"_id": {"$in": ids}, **still})
        if not res.deleted_count:
            return 0
        alive = {d["_id"] for d in await db[JOBS].find({"_id": {"$in": ids}}, {"_id": 1}).to_list(None)}
        gone = [i for i in ids if i not in alive]
        if gone:
            await db[BODIES].delete_many({"_id": {"$in": gone}})
        return res.deleted_count


store = JobStore()
