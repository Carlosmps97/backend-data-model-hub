"""Orquestación de la carga masiva (doc 55 §3.2, §6-7 · doc 105): job de
validación, guards de changeset/owner, apply con RE-validación, tandas en
orden, lock por versión y fallos legibles. Loader y alta de cambios
mockeados; parser/planner reales; jobs y cabecera del changeset en una BD en
memoria — «otro worker» = sin las tasks locales (`service._TASKS`)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.db import client as db_client
from app.features.bulk_upload import service
from app.features.bulk_upload.context import UploadContext
from app.features.bulk_upload.jobs import JOB_STALE_SECONDS
from app.features.bulk_upload.schemas import RawRow, RawSheet, UploadWorkbookBody
from app.features.changesets import repository as cs_repository
from app.features.changesets.validation import DuplicateEntityError
from tests.support.fakedb import FakeDb

PROFILE = {"id": "pf", "name": "Perfil test", "sheets": {
    "tables": {"name": "Tablas", "required": True, "headerRow": 2, "mappings": [
        {"header": "TABLA_LOGICO", "target": {"kind": "field", "field": "logicalName"}},
        {"header": "ESQUEMA", "target": {"kind": "field", "field": "schema"}},
        {"header": "PROJECT", "target": {"kind": "field", "field": "project"}},
        {"header": "DIAGRAMA", "target": {"kind": "field", "field": "diagram"}}]},
    "columns": {"name": "Atributos", "required": False, "headerRow": 2, "mappings": [
        {"header": "TABLA_LOGICO", "target": {"kind": "field", "field": "tableRef"}},
        {"header": "CAMPO_LOGICO", "target": {"kind": "field", "field": "logicalName"}}]}},
    "policies": {"onExistingTable": "update", "onExistingColumn": "update", "unknownHeaders": "warn"}}


def _body(n_tables: int = 1, schema: str = "ddv", profile_id: str = "pf") -> UploadWorkbookBody:
    rows = [RawRow(row=2, cells=["TABLA_LOGICO", "ESQUEMA", "PROJECT", "DIAGRAMA"])]
    rows += [RawRow(row=3 + i, cells=[f"Tabla {i}", schema, "P", "D"]) for i in range(n_tables)]
    return UploadWorkbookBody(fileName="carga.xlsx", profileId=profile_id, sheets=[RawSheet(name="Tablas", rows=rows)])


def _ctx(schema_kind: str = "tables") -> UploadContext:
    return UploadContext(
        project_id="p1", project_name="P",
        schemas=[{"id": "s1", "name": "ddv", "kind": schema_kind}],
        naming={s: {"scope": s, "separator": "", "case": "upper", "maxLength": 150} for s in ("table", "column")},
        glossary={"table": {}, "column": {}},
    )


@pytest.fixture
def env(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", db)
    db.raw["changesets"].insert_one({"_id": "c1", "title": "c1", "projectId": "p1", "status": "draft", "owner": "ana"})
    service._TASKS.clear()
    state = {"db": db, "ctx": _ctx(), "batches": []}
    monkeypatch.setattr(service.loader, "load_context", AsyncMock(side_effect=lambda cs_id: state["ctx"]))
    monkeypatch.setattr(service.loader, "load_columns", AsyncMock(return_value={}))
    state["profile"] = PROFILE
    monkeypatch.setattr(service.loader, "load_profile",
                        AsyncMock(side_effect=lambda pid, profile_id: state["profile"] if (pid, profile_id) == ("p1", "pf") else None))

    async def _bulk(cs_id, actor, items):
        state["batches"].append((cs_id, actor, list(items)))
        return {"id": cs_id, "status": "draft"}

    state["bulk"] = AsyncMock(side_effect=_bulk)
    monkeypatch.setattr(service.cs_service, "add_changes_bulk", state["bulk"])
    return state


def _set_cs(env, **fields) -> None:
    env["db"].raw["changesets"].update_one({"_id": "c1"}, {"$set": fields})


def _header(env) -> dict:
    return env["db"].raw["changesets"].find_one({"_id": "c1"})


def _other_worker() -> None:
    """Lo que sigue corre como en el OTRO proceso de uvicorn: no conoce las
    tasks de éste; sólo la BD es común."""
    service._TASKS.clear()


async def _wait(job_view: dict) -> dict:
    task = service._TASKS.get(job_view["id"])
    if task is not None:
        await task
    return service.store.view(await service.store.get(job_view["id"]))


def test_start_validation_crea_job_y_termina_validated(env):
    async def run():
        v = await service.start_validation("c1", "ana", _body())
        assert v["status"] == "validating"
        done = await _wait(v)
        assert done["status"] == "validated" and done["error"] is None
        assert done["report"]["summary"]["tables"] == {"create": 1, "update": 0, "unchanged": 0}
        assert done["report"]["errorCount"] == 0
        assert done["progress"]["done"] == done["progress"]["total"]

    asyncio.run(run())


def test_guards_de_changeset(env):
    async def run():
        assert await service.start_validation("c9", "ana", _body()) is None
        assert await service.start_validation("c1", "beto", _body()) == "forbidden"
        assert await service.start_validation("c1", "ana", _body(profile_id="zz")) == "profile-not-found"
        _set_cs(env, status="submitted")
        assert await service.start_validation("c1", "ana", _body()) == "locked"

    asyncio.run(run())


def test_get_job_respeta_owner_y_changeset(env):
    async def run():
        v = await service.start_validation("c1", "ana", _body())
        await _wait(v)
        assert (await service.get_job("c1", "beto", v["id"])) == "forbidden"
        assert (await service.get_job("c2", "ana", v["id"])) is None
        assert (await service.get_job("c1", "ana", "nope")) is None
        assert (await service.get_job("c1", "ana", v["id"]))["status"] == "validated"

    asyncio.run(run())


def test_apply_exige_validated_sin_errores(env):
    async def run():
        v = await service.start_validation("c1", "ana", _body())
        assert await service.start_apply("c1", "ana", v["id"]) == "not-validated"
        await _wait(v)
        env["ctx"] = _ctx(schema_kind="views")
        v2 = await service.start_validation("c1", "ana", _body())
        done = await _wait(v2)
        assert done["report"]["errorCount"] == 1
        assert await service.start_apply("c1", "ana", v2["id"]) == "has-errors"

    asyncio.run(run())


def test_apply_re_valida_y_escribe_en_tandas_en_orden(env, monkeypatch):
    monkeypatch.setattr(service, "APPLY_BATCH", 2)

    async def run():
        v = await service.start_validation("c1", "ana", _body(n_tables=3))
        await _wait(v)
        a = await service.start_apply("c1", "ana", v["id"])
        assert a["status"] == "applying"
        done = await _wait(a)
        assert done["status"] == "applied", done["error"]
        # 3 tablas + canvas = 4 cambios (doc 75: el proyecto es el del changeset,
        # nunca se crea) → tandas de 2, 2; el canvas va AL FINAL (doc 87 §3.4:
        # referencia tablas y vistas, que ya están pendientes cuando llega su tanda)
        colls = [ch["collection"] for _, _, items in env["batches"] for ch in items]
        assert colls == ["canonical_tables", "canonical_tables", "canonical_tables", "subject_areas"]
        assert [len(items) for _, _, items in env["batches"]] == [2, 2]
        assert all(actor == "ana" for _, actor, _ in env["batches"])
        assert len(done["result"]["affectedCanvasIds"]) == 1
        assert done["result"]["counts"]["tables"]["create"] == 3
        assert env["bulk"].await_count == 2

    asyncio.run(run())


def test_apply_aborta_si_la_revalidacion_trae_errores(env):
    async def run():
        v = await service.start_validation("c1", "ana", _body())
        await _wait(v)
        env["ctx"] = _ctx(schema_kind="views")          # el modelo cambió entre Validate y Upload
        a = await service.start_apply("c1", "ana", v["id"])
        done = await _wait(a)
        assert done["status"] == "failed"
        assert "nothing was written" in done["error"]
        assert done["report"]["errorCount"] == 1        # reporte FRESCO
        env["bulk"].assert_not_called()

    asyncio.run(run())


def test_apply_busy_si_hay_otro_apply_en_curso(env):
    async def run():
        v = await service.start_validation("c1", "ana", _body())
        await _wait(v)
        assert await cs_repository.claim_upload_lock("c1", "ana", "otra-carga") is not None
        assert await service.start_apply("c1", "ana", v["id"]) == "busy"
        assert (await service.get_job("c1", "ana", v["id"]))["status"] == "validated"   # sigue lista

    asyncio.run(run())


def test_apply_falla_legible_si_el_bulk_rechaza(env):
    env["bulk"].side_effect = DuplicateEntityError("Table TABLA0 already exists")

    async def run():
        v = await service.start_validation("c1", "ana", _body())
        await _wait(v)
        done = await _wait(await service.start_apply("c1", "ana", v["id"]))
        assert done["status"] == "failed" and "already exists" in done["error"]

    asyncio.run(run())


def test_apply_falla_si_el_draft_ya_no_acepta_cambios(env):
    env["bulk"].side_effect = None
    env["bulk"].return_value = "locked"

    async def run():
        v = await service.start_validation("c1", "ana", _body())
        await _wait(v)
        done = await _wait(await service.start_apply("c1", "ana", v["id"]))
        assert done["status"] == "failed" and "no longer in draft" in done["error"]

    asyncio.run(run())


def test_apply_cortado_porque_la_version_cambio_de_manos(env, monkeypatch):
    """Doc 104: si la versión se transfiere (o se elimina) mientras la carga se
    escribe, el job lo dice — y cuántas tandas alcanzaron a grabarse."""
    monkeypatch.setattr(service, "APPLY_BATCH", 2)
    answers = iter([{"id": "c1", "status": "draft"}, "forbidden"])
    env["bulk"].side_effect = lambda cs_id, actor, items: next(answers)

    async def run():
        v = await service.start_validation("c1", "ana", _body(n_tables=3))
        await _wait(v)
        done = await _wait(await service.start_apply("c1", "ana", v["id"]))
        assert done["status"] == "failed"
        assert "transferred" in done["error"] and "1 of 2 batches" in done["error"]

    asyncio.run(run())


def test_apply_cortado_porque_la_version_se_elimino(env):
    env["bulk"].side_effect = None
    env["bulk"].return_value = None

    async def run():
        v = await service.start_validation("c1", "ana", _body())
        await _wait(v)
        done = await _wait(await service.start_apply("c1", "ana", v["id"]))
        assert done["status"] == "failed" and "was deleted" in done["error"] and "nothing of it is kept" in done["error"]

    asyncio.run(run())


def test_apply_cortado_porque_la_version_se_elimino_a_mitad(env, monkeypatch):
    """Ronda 3: eliminar la versión borra también lo que la carga alcanzó a
    escribir — el mensaje no debe decir que quedó guardado."""
    monkeypatch.setattr(service, "APPLY_BATCH", 2)
    answers = iter([{"id": "c1", "status": "draft"}, None])
    env["bulk"].side_effect = lambda cs_id, actor, items: next(answers)

    async def run():
        v = await service.start_validation("c1", "ana", _body(n_tables=3))
        await _wait(v)
        done = await _wait(await service.start_apply("c1", "ana", v["id"]))
        assert done["status"] == "failed" and "was deleted" in done["error"]
        assert "saved" not in done["error"]

    asyncio.run(run())


def test_validation_falla_legible_si_el_loader_revienta(env):
    service.loader.load_context.side_effect = RuntimeError("db down")

    async def run():
        done = await _wait(await service.start_validation("c1", "ana", _body()))
        assert done["status"] == "failed" and done["error"] == "db down"

    asyncio.run(run())


def test_discard(env):
    async def run():
        v = await service.start_validation("c1", "ana", _body())
        assert await service.discard("c1", "beto", v["id"]) == "forbidden"
        assert await service.discard("c1", "ana", v["id"]) is True
        assert await service.discard("c1", "ana", v["id"]) is False

    asyncio.run(run())


def test_discard_no_cancela_un_apply_en_curso(env):
    async def run():
        v = await service.start_validation("c1", "ana", _body())
        await _wait(v)
        await service.store.claim_apply(v["id"])     # a mitad de las tandas (en cualquier proceso)
        assert await service.discard("c1", "ana", v["id"]) == "busy"
        assert await service.store.get(v["id"]) is not None

    asyncio.run(run())


def test_discard_condicionado_a_validated_no_borra_lo_que_ya_empezo_doc105(env):
    async def run():
        v = await service.start_validation("c1", "ana", _body())
        await _wait(v)
        await service.store.claim_apply(v["id"])
        assert await service.discard("c1", "ana", v["id"], only_status="validated") == "changed"
        assert await service.store.get(v["id"]) is not None
        w = await service.start_validation("c1", "ana", _body())
        await _wait(w)
        assert await service.discard("c1", "ana", w["id"], only_status="validated") is True

    asyncio.run(run())


def test_validation_falla_legible_si_el_perfil_desaparece_entre_medio(env):
    async def run():
        v = await service.start_validation("c1", "ana", _body())
        env["profile"] = None                       # borrado entre el POST y el job
        done = await _wait(v)
        assert done["status"] == "failed" and "Upload profile not found" in done["error"]

    asyncio.run(run())


def test_validate_workbook_reporta_perfil_invalido_como_error_de_perfil(env):
    env["profile"] = {**PROFILE, "sheets": {**PROFILE["sheets"], "tables": {**PROFILE["sheets"]["tables"], "mappings": [
        *PROFILE["sheets"]["tables"]["mappings"], {"header": "U", "target": {"kind": "udp", "udpIds": ["borrada"]}}]}}}

    async def run():
        done = await _wait(await service.start_validation("c1", "ana", _body()))
        assert done["status"] == "validated"
        (e,) = done["report"]["errors"]
        assert (e["sheet"], e["code"]) == ("Profile", "profile-udp-missing") and "Perfil test" in e["message"]
        assert done["report"]["profile"] == {"id": "pf", "name": "Perfil test"}
        assert done["report"]["sheets"][0]["name"] == "Tablas"

    asyncio.run(run())


# ── Doc 87 §3.5: proyecto destino ──────────────────────────────────────────
_DDV_FOLDERS = [{"id": "c", "projectId": "p1", "parentFolderId": None, "name": "CPYBCA", "order": 0},
                {"id": "o", "projectId": "p1", "parentFolderId": None, "name": "Otros", "order": 1},
                {"id": "x", "projectId": "p1", "parentFolderId": "c", "name": "SA1", "order": 0},
                {"id": "y", "projectId": "p1", "parentFolderId": "o", "name": "SA2", "order": 0}]


def test_targets_devuelve_la_capa_de_proyectos_internos_con_los_guards(env, monkeypatch):
    monkeypatch.setattr(service.loader, "load_folders", AsyncMock(return_value=_DDV_FOLDERS))

    async def run():
        assert await service.targets("c9", "ana") is None
        assert await service.targets("c1", "beto") == "forbidden"
        t = await service.targets("c1", "ana")
        assert t["mode"] == "choose" and [c["name"] for c in t["candidates"]] == ["CPYBCA", "Otros"]
        _set_cs(env, status="submitted")
        assert await service.targets("c1", "ana") == "locked"

    asyncio.run(run())


def test_validation_respeta_el_proyecto_destino_del_body(env):
    c = _ctx()
    c.folders = list(_DDV_FOLDERS)
    env["ctx"] = c

    async def run():
        done = await _wait(await service.start_validation("c1", "ana", _body()))
        assert [e["code"] for e in done["report"]["errors"]] == ["target-folder-required"]
        body = _body()
        body.targetFolderId = "o"
        done = await _wait(await service.start_validation("c1", "ana", body))
        assert done["report"]["errorCount"] == 0
        canvas = next(ch for ch in await _plan_changes(done) if ch["collection"] == "subject_areas")
        assert canvas["payload"]["folderId"] == "o"

    asyncio.run(run())


async def _plan_changes(job_view: dict) -> list[dict]:
    """Los cambios planificados no viajan en el view del job: se re-planifican
    con el mismo body para inspeccionarlos (validate_workbook es determinista)."""
    from app.features.bulk_upload.planner import build_plan
    from app.features.bulk_upload.profiles.apply import apply_profile
    body = await service.store.load_body(job_view["id"])
    ctx = service.loader.load_context.side_effect("c1")
    parsed = apply_profile(body, PROFILE, ctx.udp_defs)
    return build_plan(parsed, ctx, target_folder_id=body.targetFolderId).changes


# ── Doc 105: `uvicorn --workers 2` — cada request puede caer en otro proceso ──


def test_otro_worker_consulta_aplica_y_el_lock_se_suelta(env):
    async def run():
        v = await service.start_validation("c1", "ana", _body(n_tables=2))
        await _wait(v)
        _other_worker()
        assert (await service.get_job("c1", "ana", v["id"]))["status"] == "validated"   # antes: 404
        a = await service.start_apply("c1", "ana", v["id"])
        assert a["status"] == "applying" and _header(env)["uploadLock"]["jobId"] == v["id"]
        done = await _wait(a)
        assert done["status"] == "applied", done["error"]
        assert _header(env).get("uploadLock") is None
        assert await service.store.load_body(v["id"]) is None                          # ya no sirve

    asyncio.run(run())


def test_dos_cargas_a_la_vez_en_la_misma_version_una_espera(env):
    async def run():
        v1 = await service.start_validation("c1", "ana", _body())
        v2 = await service.start_validation("c1", "ana", _body())
        await _wait(v1)
        await _wait(v2)
        r1, r2 = await asyncio.gather(service.start_apply("c1", "ana", v1["id"]),
                                      service.start_apply("c1", "ana", v2["id"]))
        outs = [r1, r2]
        assert outs.count("busy") == 1, outs
        started = next(r for r in outs if isinstance(r, dict))
        waiting = v2 if started["id"] == v1["id"] else v1
        assert (await service.get_job("c1", "ana", waiting["id"]))["status"] == "validated"
        await _wait(started)
        assert (await service.start_apply("c1", "ana", waiting["id"]))["status"] == "applying"   # ahora sí
        await _wait(waiting)

    asyncio.run(run())


def test_el_mismo_job_aplicado_dos_veces_a_la_vez_escribe_una_sola(env):
    async def run():
        v = await service.start_validation("c1", "ana", _body())
        await _wait(v)
        r1, r2 = await asyncio.gather(service.start_apply("c1", "ana", v["id"]),
                                      service.start_apply("c1", "ana", v["id"]))
        assert sorted([isinstance(r1, dict), isinstance(r2, dict)]) == [False, True]
        await _wait(v)
        assert env["bulk"].await_count == 1

    asyncio.run(run())


def test_el_lock_se_suelta_aunque_el_apply_falle(env):
    env["bulk"].side_effect = DuplicateEntityError("Table TABLA0 already exists")

    async def run():
        v = await service.start_validation("c1", "ana", _body())
        await _wait(v)
        done = await _wait(await service.start_apply("c1", "ana", v["id"]))
        assert done["status"] == "failed" and _header(env).get("uploadLock") is None

    asyncio.run(run())


def test_cada_tanda_es_un_latido_del_lock(env, monkeypatch):
    monkeypatch.setattr(service, "APPLY_BATCH", 1)
    beats = []
    real = cs_repository.heartbeat_upload_lock

    async def spy(cs_id, job_id):
        beats.append(job_id)
        return await real(cs_id, job_id)

    monkeypatch.setattr(service.cs_repository, "heartbeat_upload_lock", spy)

    async def run():
        v = await service.start_validation("c1", "ana", _body(n_tables=3))
        await _wait(v)
        done = await _wait(await service.start_apply("c1", "ana", v["id"]))
        assert done["status"] == "applied"
        assert len(beats) >= 4 + 6                  # 4 tandas + 6 pasos de la re-validación

    asyncio.run(run())


def test_validacion_descartada_desde_otro_worker_no_revive(env):
    async def run():
        v = await service.start_validation("c1", "ana", _body())
        task = service._TASKS[v["id"]]
        _other_worker()                              # el descarte no puede cancelar la task ajena
        assert await service.discard("c1", "ana", v["id"]) is True
        await task                                   # termina sola...
        assert await service.get_job("c1", "ana", v["id"]) is None   # ...y no revive el job

    asyncio.run(run())


def test_carga_cuyo_proceso_murio_se_informa_y_no_traba_la_version(env, monkeypatch):
    async def run():
        v = await service.start_validation("c1", "ana", _body())
        await _wait(v)
        await service.store.claim_apply(v["id"])
        assert await cs_repository.claim_upload_lock("c1", "ana", v["id"]) is not None
        # … y el proceso muere: nadie avanza el job ni late el lock.
        later = service.store.now() + JOB_STALE_SECONDS + 5
        monkeypatch.setattr(service.store, "_clock", lambda: later)
        monkeypatch.setattr(cs_repository, "_clock", lambda: later)
        dead = await service.get_job("c1", "ana", v["id"])
        assert dead["status"] == "failed" and "stopped unexpectedly" in dead["error"]
        assert await service.discard("c1", "ana", v["id"]) is True
        v2 = await service.start_validation("c1", "ana", _body())
        await _wait(v2)
        assert (await service.start_apply("c1", "ana", v2["id"]))["status"] == "applying"   # lock muerto: se reemplaza
        await _wait(v2)

    asyncio.run(run())


def test_carrera_otra_carga_toma_la_version_entre_la_lectura_y_el_claim(env, monkeypatch):
    """El pre-chequeo leyó la cabecera SIN lock; otro proceso lo tomó justo
    después: el claim condicionado debe negarse (y el job sigue listo)."""
    async def run():
        v = await service.start_validation("c1", "ana", _body())
        await _wait(v)
        stale = dict(await service.cs_service.get("c1"))
        assert await cs_repository.claim_upload_lock("c1", "ana", "otra-carga") is not None
        monkeypatch.setattr(service.cs_service, "get", AsyncMock(return_value=stale))
        assert await service.start_apply("c1", "ana", v["id"]) == "busy"
        assert _header(env)["uploadLock"]["jobId"] == "otra-carga"          # el ajeno sigue intacto
        assert (await service.store.get(v["id"]))["status"] == "validated"
        env["bulk"].assert_not_called()

    asyncio.run(run())


def test_si_otra_carga_toma_el_lock_vencido_esta_se_corta_sin_escribir_mas(env, monkeypatch):
    """Doc 105 (R1): el latido no verificaba que el lock siguiera siendo del
    job. Si un paso tardó más que el vencimiento y otra carga tomó la versión,
    las dos escribían a la vez."""
    monkeypatch.setattr(service, "APPLY_BATCH", 1)

    async def run():
        v = await service.start_validation("c1", "ana", _body(n_tables=3))
        await _wait(v)
        real_bulk = env["bulk"].side_effect

        async def bulk(cs_id, actor, items):
            out = await real_bulk(cs_id, actor, items)
            if len(env["batches"]) == 2:              # tras la 2.ª tanda, otra carga toma la versión
                _set_cs(env, uploadLock={"jobId": "otra", "owner": "ana", "at": 0, "heartbeat": service.store.now()})
            return out

        env["bulk"].side_effect = bulk
        done = await _wait(await service.start_apply("c1", "ana", v["id"]))
        assert done["status"] == "failed"
        assert "Another upload took over this version" in done["error"], done["error"]
        assert "2 of 4 batches had already been saved" in done["error"], done["error"]
        assert len(env["batches"]) == 2
        assert _header(env)["uploadLock"]["jobId"] == "otra"          # el lock ajeno no se suelta

    asyncio.run(run())


def test_latido_del_lock_dice_si_sigue_siendo_del_job(env):
    async def run():
        assert await cs_repository.claim_upload_lock("c1", "ana", "j1") is not None
        assert await cs_repository.heartbeat_upload_lock("c1", "j1") is True
        assert await cs_repository.heartbeat_upload_lock("c1", "j2") is False
        assert await cs_repository.heartbeat_upload_lock("no-existe", "j1") is False

    asyncio.run(run())
