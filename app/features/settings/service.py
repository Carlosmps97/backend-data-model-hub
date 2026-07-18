"""Negocio de `settings` (naming_config): lectura por scope + upsert.

Validación de scope/case acá (agnóstica al store). El motor de naming consume
estos valores vía el service de `dictionary`.
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


async def get_naming() -> dict[str, dict]:
    """Ambos scopes (con defaults sembrados). Forma: {column: {...}, table: {...}}."""
    return await repository.get_all()


async def get_naming_for(scope: str) -> dict:
    """Config de un scope (default sembrado). Usado por el motor de naming."""
    _validate_scope(scope)
    return await repository.get_one(scope)


async def update_naming(scope: str, body: NamingConfigBody) -> dict:
    """Upsert de {separator, case} para `scope`."""
    _validate_scope(scope)
    _validate_case(body.case)
    return await repository.upsert(scope, body.model_dump())
