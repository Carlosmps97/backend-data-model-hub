"""Endpoints de DDL Export Rules. Solo LECTURA — las mutaciones son versionadas
vía `POST /api/standards/apply` (rulesUpsert/rulesDelete/ddlConfigPatch), como
Glossary/Parent Domains/UDP. La lectura queda abierta (la usan el catálogo de
la pestaña, el bench del editor y el modal de Export DDL)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.api.envelope import ok
from app.features.auth.deps import require_permission
from app.features.data_standards.schemas import DdlRuleEdit

from . import service
from .schemas import ImpactBody, RenderBody, TestBody

router = APIRouter(prefix="/api/ddl-rules", tags=["ddl-rules"])


@router.get("")
async def list_rules():
    """Reglas activas en orden de ejecución (priority DESC, name ASC)."""
    return ok(await service.list_rules())


@router.get("/config")
async def get_config():
    """Config del ruleset global: lookups + funciones reusables."""
    return ok(await service.get_config())


@router.get("/artifacts")
async def artifacts():
    """Catálogo de artefactos: raíces + declarados por generadores activos."""
    rules = await service.list_rules()
    return ok(service.artifact_catalog(rules))


@router.post("/validate")
async def validate(body: DdlRuleEdit):
    """Los 5 checks del bench (spec §9) sobre la regla del body. NO guarda —
    el guardado versionado va por POST /api/standards/apply."""
    return ok(await service.validate_payload(body.model_dump()))


@router.get("/templates")
async def templates():
    """Plantillas del picker (16d) + las 7 reglas semilla del spec §8 con el
    lookup vacuum_map resuelto a los UDP ids de esta BD."""
    return ok(await service.templates_payload())


@router.post("/test")
async def test_rule(body: TestBody):
    """Corre la regla contra una tabla REAL del catálogo (spec 15.4). Solo
    devuelve los fragmentos de lo que matchea — nunca el DDL completo."""
    return ok(await service.test_rule(body.rule.model_dump(), body.tableId))


@router.post("/impact")
async def impact(body: ImpactBody):
    """'Matches N columns across M tables' cuando se aplique en el export."""
    return ok(await service.impact(body.rule.model_dump()))


@router.post("/render")
async def render(body: RenderBody, user: dict = Depends(require_permission("export"))):
    """Puente del Export DDL (doc 30 §8): motor PURO sobre el payload del
    canvas (estado efectivo). Devuelve statements + log + la versión de
    standards usada (evidencia de la corrida). Requiere permiso `export`."""
    return ok(await service.render_export_payload(user["username"], body.model_dump()))
