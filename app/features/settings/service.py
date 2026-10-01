"""Negocio de `settings` (naming_config): lectura por (proyecto, scope) + upsert.

Validación de scope/case acá (agnóstica al store). El motor de naming consume
estos valores vía el service de `glossary`. Doc 75 D3: todo por proyecto.
"""
from __future__ import annotations

from app.core.naming.engine import _VALID_CASES

from . import repository
from .models import SCOPES
from .schemas import NamingConfigBody


def _validate_scope(scope: str) -> None:
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}, not {scope!r}")


def _validate_case(case: str) -> None:
    if case not in _VALID_CASES:
        raise ValueError(f"case must be one of {_VALID_CASES}, not {case!r}")


async def get_naming(project_id: str) -> dict[str, dict]:
    """Ambos scopes del proyecto (con defaults sembrados). Forma: {column: {...}, table: {...}}."""
    return await repository.get_all(project_id)


async def get_naming_stored(project_id: str) -> dict[str, dict]:
    """Lo guardado por scope, sin enmascarar valores inválidos (ver repository)."""
    return await repository.get_stored(project_id)


async def get_naming_for(project_id: str, scope: str) -> dict:
    """Config de un scope del proyecto (default sembrado). Usado por el motor de naming."""
    _validate_scope(scope)
    return await repository.get_one(project_id, scope)


async def update_naming(project_id: str, scope: str, body: NamingConfigBody) -> dict:
    """Upsert de {separator, case, maxLength} para (proyecto, scope)."""
    _validate_scope(scope)
    _validate_case(body.case)
    return await repository.upsert(project_id, scope, body.model_dump())
