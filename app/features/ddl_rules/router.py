"""Endpoints de DDL Export Rules. Solo LECTURA — las mutaciones son versionadas
vía `POST /api/projects/{pid}/standards/apply` (rulesUpsert/rulesDelete/
ddlConfigPatch), como Glossary/Parent Domains/UDP. La lectura queda abierta (la
usan el catálogo de la pestaña, el bench del editor y el modal de Export DDL).
Doc 75 D3/D4: todo POR PROYECTO — prefijo `/api/projects/{project_id}/ddl-rules`."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.api.envelope import ok
from app.features.auth.deps import require_permission
from app.features.data_standards.schemas import DdlRuleEdit
from app.features.projects.deps import alive_project

from . import service
from .schemas import ImpactBody, RenderBody, TestBody

router = APIRouter(prefix="/api/projects/{project_id}/ddl-rules", tags=["ddl-rules"],
                   dependencies=[Depends(alive_project)])


@router.get("")
async def list_rules(project_id: str):
    """Reglas activas del proyecto en orden de ejecución (priority DESC, name ASC)."""
    return ok(await service.list_rules(project_id))


@router.get("/config")
async def get_config(project_id: str):
    """Config del ruleset del proyecto: lookups + funciones reusables."""
    return ok(await service.get_config(project_id))


@router.get("/artifacts")
async def artifacts(project_id: str):
    """Catálogo de artefactos: raíces + declarados por generadores activos."""
    rules = await service.list_rules(project_id)
    return ok(service.artifact_catalog(rules))


@router.post("/validate")
async def validate(project_id: str, body: DdlRuleEdit):
    """Los 5 checks del bench (spec §9) sobre la regla del body. NO guarda —
    el guardado versionado va por POST …/standards/apply."""
    return ok(await service.validate_payload(project_id, body.model_dump()))


@router.get("/templates")
async def templates(project_id: str):
    """Plantillas del picker (16d) + las reglas semilla del spec §8 con el
    lookup vacuum_map resuelto a los UDP ids del proyecto."""
    return ok(await service.templates_payload(project_id))


@router.post("/test")
async def test_rule(project_id: str, body: TestBody):
    """Corre la regla contra una tabla REAL del catálogo (spec 15.4). Solo
    devuelve los fragmentos de lo que matchea — nunca el DDL completo."""
    return ok(await service.test_rule(project_id, body.rule.model_dump(), body.tableId))


@router.post("/impact")
async def impact(project_id: str, body: ImpactBody):
    """'Matches N columns across M tables' cuando se aplique en el export."""
    return ok(await service.impact(project_id, body.rule.model_dump()))


@router.post("/render")
async def render(project_id: str, body: RenderBody,
                 user: dict = Depends(require_permission("export"))):
    """Puente del Export DDL (doc 30 §8): motor PURO sobre el payload del
    canvas (estado efectivo). Devuelve statements + log + la versión de
    standards del proyecto usada (evidencia de la corrida). Requiere `export`."""
    return ok(await service.render_export_payload(user["username"], project_id, body.model_dump()))
