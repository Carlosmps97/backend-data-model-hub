"""Endpoints del diccionario de abreviaturas + conversión lógico↔físico."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.core.api.envelope import ok
from app.features.auth.deps import require_permission

from . import service
from .schemas import (
    AbbreviationBody,
    LogicalizeBody,
    PhysicalizeBody,
    RephysicalizeBody,
    ValidateTermBody,
)

# Editar términos del diccionario ES editar estándares → standards.edit. Los
# endpoints de CÓMPUTO (physicalize/logicalize) quedan abiertos (los usa el
# modelador para previsualizar nombres; no mutan).
router = APIRouter(prefix="/api/glossary", tags=["glossary"])
_std = require_permission("standards.edit")


@router.get("")
async def list_entries(scope: str | None = Query(default=None)):
    """Lista términos. Sin `scope` = todos (compat); con `scope` = sólo ese
    ('column' | 'table')."""
    return ok(await service.list_entries(scope))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_entry(body: AbbreviationBody, user: dict = Depends(_std)):
    return ok(await service.create_entry(body))


@router.put("/{entry_id}")
async def update_entry(entry_id: str, body: AbbreviationBody, user: dict = Depends(_std)):
    return ok(await service.update_entry(entry_id, body))


@router.delete("/{entry_id}")
async def delete_entry(entry_id: str, user: dict = Depends(_std)):
    return ok(await service.delete_entry(entry_id))


@router.post("/physicalize")
async def physicalize_name(body: PhysicalizeBody):
    physical = await service.physicalize_name(
        body.logical, scope=body.scope, separator=body.separator
    )
    return ok({"physical": physical})


@router.post("/logicalize")
async def logicalize_name(body: LogicalizeBody):
    return ok({"logical": await service.logicalize_name(body.physical)})


@router.post("/rephysicalize")
async def rephysicalize(body: RephysicalizeBody, user: dict = Depends(_std)):
    """Re-physicalize retroactivo (R5): recomputa el `physicalName` de TODAS las
    entidades del scope desde su `logicalName`. Sin scope ⇒ tablas y columnas.
    Update directo (fuera de publish). Devuelve `{updated: {tables, columns}}`."""
    return ok(await service.rephysicalize(body.scope))


@router.post("/validate")
async def validate_term(body: ValidateTermBody):
    """F2 #1: valida un término NUEVO contra el glosario del scope (duplicado
    exacto) y contra los nombres lógicos publicados (frase completa, muestra
    cap 50 + total). No muta; el enforcement real vive en los writes
    (POST/PUT de este router y standards/apply)."""
    # Término vacío/whitespace: `corpus_regex('')` es laxo, así que cortamos acá
    # y devolvemos el contrato 'sin conflictos' sin tocar el service/DB.
    term = body.term.strip()
    if not term:
        return ok({"ok": True,
                   "conflicts": {"glossaryDuplicate": None, "corpus": [], "total": 0}})
    return ok(await service.validate_term(term, body.scope))
