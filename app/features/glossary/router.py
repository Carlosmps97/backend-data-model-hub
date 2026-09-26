"""Endpoints del diccionario de abreviaturas + conversión lógico↔físico."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.api.envelope import ok
from app.core.identity import Principal, current_principal
from app.features.auth.deps import require_permission
from app.features.projects.deps import alive_project

from . import service
from .schemas import (
    AbbreviationBody,
    ImpactBody,
    PhysicalizeBody,
    RephysicalizeBody,
    ValidateTermBody,
)

# Editar términos del diccionario ES editar estándares → standards.edit. Los
# endpoint de CÓMPUTO (physicalize) queda abierto (lo usa el
# modelador para previsualizar nombres; no mutan).
# Doc 75 D3/D4: glosario POR PROYECTO — prefijo `/api/projects/{project_id}/glossary`.
router = APIRouter(prefix="/api/projects/{project_id}/glossary", tags=["glossary"],
                   dependencies=[Depends(alive_project)])
_std = require_permission("standards.edit")
# Lock/unlock del glosario: SOLO admin (D4) — no alcanza standards.edit.
_admin = require_permission("admin.manage")


@router.get("")
async def list_entries(project_id: str, scope: str | None = Query(default=None)):
    """Lista términos del proyecto. Sin `scope` = todos; con `scope` = sólo ese
    ('column' | 'table')."""
    return ok(await service.list_entries(project_id, scope))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_entry(project_id: str, body: AbbreviationBody, user: dict = Depends(_std)):
    return ok(await service.create_entry(project_id, body))


@router.put("/{entry_id}")
async def update_entry(project_id: str, entry_id: str, body: AbbreviationBody,
                       user: dict = Depends(_std)):
    return ok(await service.update_entry(project_id, entry_id, body))


@router.delete("/{entry_id}")
async def delete_entry(entry_id: str, user: dict = Depends(_std)):
    return ok(await service.delete_entry(entry_id))


@router.post("/physicalize")
async def physicalize_name(project_id: str, body: PhysicalizeBody):
    physical = await service.physicalize_name(
        project_id, body.logical, scope=body.scope, separator=body.separator
    )
    return ok({"physical": physical})


@router.post("/rephysicalize")
async def rephysicalize(project_id: str, body: RephysicalizeBody, user: dict = Depends(_std)):
    """Re-physicalize retroactivo (R5): recomputa el `physicalName` de TODAS las
    entidades del scope DEL PROYECTO desde su `logicalName`. Sin scope ⇒ tablas y
    columnas. Update directo (fuera de publish). Devuelve `{updated: {tables, columns}}`."""
    return ok(await service.rephysicalize(project_id, body.scope))


@router.post("/validate")
async def validate_term(project_id: str, body: ValidateTermBody,
                        principal: Principal = Depends(current_principal)):
    """F2 #1: valida un término NUEVO contra el glosario del scope (duplicado
    exacto) y contra los nombres lógicos publicados (frase completa, muestra
    cap 50 + total). No muta; el enforcement real vive en los writes
    (POST/PUT de este router y standards/apply). Exige SESIÓN (lee el catálogo:
    en prod un anónimo no debe enumerar tablas/columnas) pero NO standards.edit
    — el botón Validar del front lo usan también usuarios sin ese permiso."""
    # Término vacío/whitespace: `corpus_regex('')` es laxo, así que cortamos acá
    # y devolvemos el contrato 'sin conflictos' sin tocar el service/DB.
    term = body.term.strip()
    if not term:
        return ok({"ok": True,
                   "conflicts": {"glossaryDuplicate": None, "corpus": [], "total": 0}})
    # Doc 95 D1: el popup muestra TODAS las columnas en conflicto (lista completa).
    return ok(await service.validate_term(project_id, term, body.scope, limit=None))


@router.post("/impact")
async def impact(project_id: str, body: ImpactBody,
                 principal: Principal = Depends(current_principal)):
    """Docs 94 D7 · 95 D3: dry-run del re-derivado que haría aplicar el borrador
    del glosario — `{renamed, outOfSync}` con las listas completas. No muta.
    Sesión, sin standards.edit (igual que /validate): el front decide con esto si
    avisa."""
    return ok(await service.impact_preview(
        project_id, body.scope,
        [t.model_dump(exclude_none=True) for t in body.termsUpsert], body.termsDelete,
        body.namingConfig.model_dump(exclude_none=True) if body.namingConfig else None))


@router.post("/{entry_id}/lock")
async def lock_entry(entry_id: str, user: dict = Depends(_admin)):
    """F2 #1 (D4): bloquea la entrada — intocable para TODOS (409 en editar/
    eliminar, CRUD o apply) hasta que un admin la desbloquee. Audita."""
    entry = await service.set_lock(entry_id, True, user["username"])
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="The term doesn't exist.")
    return ok(entry)


@router.post("/{entry_id}/unlock")
async def unlock_entry(entry_id: str, user: dict = Depends(_admin)):
    """F2 #1 (D4): desbloquea la entrada. Audita."""
    entry = await service.set_lock(entry_id, False, user["username"])
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="The term doesn't exist.")
    return ok(entry)
