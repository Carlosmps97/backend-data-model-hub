"""Orquestación de la carga masiva (doc 55 §3.2, §6-7): job de validación,
guards de changeset/owner, apply con RE-validación, tandas en orden, lock por
changeset y fallos legibles. Loader y changesets mockeados; parser/planner
reales."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.bulk_upload import service
from app.features.bulk_upload.context import UploadContext
from app.features.bulk_upload.jobs import JobRegistry
from app.features.bulk_upload.schemas import RawRow, RawSheet, UploadWorkbookBody
from app.features.changesets.validation import DuplicateEntityError

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
    state = {"ctx": _ctx(), "cs": {"id": "c1", "projectId": "p1", "status": "draft", "owner": "ana"}, "batches": []}
    monkeypatch.setattr(service, "registry", JobRegistry())
    monkeypatch.setattr(service.cs_service, "get", AsyncMock(side_effect=lambda cs_id: state["cs"] if cs_id == "c1" else None))
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


async def _wait(job_view: dict) -> dict:
    job = service.registry.get(job_view["id"])
    assert job is not None and job.task is not None
    await job.task
    return job.view()


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
        env["cs"] = {"id": "c1", "projectId": "p1", "status": "submitted", "owner": "ana"}
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
        # canvas + 3 tablas = 4 cambios (doc 75: el proyecto es el del changeset,
        # nunca se crea) → tandas de 2, 2, en orden de dependencia
        colls = [ch["collection"] for _, _, items in env["batches"] for ch in items]
        assert colls == ["subject_areas", "canonical_tables", "canonical_tables", "canonical_tables"]
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
        async with service.registry.lock_for("c1"):
            assert await service.start_apply("c1", "ana", v["id"]) == "busy"

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
        job = service.registry.get(v["id"])
        job.status = "applying"                     # a mitad de las tandas
        assert await service.discard("c1", "ana", v["id"]) == "busy"
        assert service.registry.get(v["id"]) is not None

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
