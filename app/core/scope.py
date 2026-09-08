"""Alcance por PROYECTO (doc 75 D1).

Todo documento que describe el modelo o sus estándares pertenece a exactamente
un proyecto (`projectId`, estampado SIEMPRE server-side). Estos helpers son la
única forma de armar un filtro de alcance: `scoped(pid, flt)`. `PROJECT_SCOPED`
enumera las colecciones que llevan `projectId`; `published()` del ledger exige
alcance (o un filtro por id) para ellas — una query sin proyecto es una fuga
silenciosa entre proyectos, no un error de negocio.
"""
from __future__ import annotations

PROJECT_SCOPED: frozenset[str] = frozenset({
    "folders", "subject_areas", "schemas",
    "canonical_tables", "canonical_columns", "relationships", "views",
    "parent_domains", "glossary_terms", "udp_definitions", "naming_config",
    "ddl_rules", "ddl_ruleset_config",
    "changesets", "standards_versions", "saved_reports",
})

# Claves de filtro que YA acotan a un proyecto (por id de entidad o de tabla).
_SCOPING_KEYS = frozenset({"projectId", "_id", "tableId"})


class MissingProjectError(ValueError):
    """Operación de alcance de proyecto sin `projectId`. Es un bug de programación
    (nunca input del usuario): el router la deja subir como 500."""


class ProjectDeletedError(ValueError):
    """La operación apunta a un proyecto que ya fue borrado (doc 75 D5/I11).
    Los routers responden 409."""


def require_project(project_id: str | None) -> str:
    pid = (project_id or "").strip()
    if not pid:
        raise MissingProjectError("projectId is required")
    return pid


def scoped(project_id: str, flt: dict | None = None) -> dict:
    """Filtro `{projectId, **flt}` — la única forma de acotar una lectura."""
    return {"projectId": require_project(project_id), **(flt or {})}


def naming_id(project_id: str, scope: str) -> str:
    """`_id` de `naming_config`: un doc por (proyecto, scope)."""
    return f"{require_project(project_id)}:{scope}"


def assert_scoped_filter(collection: str, flt: dict | None) -> None:
    """Defensa en profundidad de `published()`: colección de alcance ⇒ el filtro
    trae `projectId`, `_id` o `tableId`."""
    if collection in PROJECT_SCOPED and not (_SCOPING_KEYS & set(flt or {})):
        raise MissingProjectError(f"published({collection!r}) requires a project-scoped filter")
