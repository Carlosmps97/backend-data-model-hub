"""Negocio de `schemas`. `name_error` es PURO y lo reusa el rename versionado
(`changesets.service.rename_schema`). Estos endpoints directos son el camino
SIN changeset — en sesión de edición el front escribe SIEMPRE vía changeset."""
from __future__ import annotations

import re

from . import repository
from .schemas import SchemaBody

# Letras/números/guión bajo empezando con letra. Acepta mayúsculas: el legacy
# `No_Definido` (pol.NO_SCHEMA de la migración Erwin) debe validar.
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class InvalidSchemaNameError(ValueError):
    """Nombre de esquema que no cumple la regla. El router la convierte en 422."""


class DuplicateSchemaError(ValueError):
    """Ya existe un esquema activo con ese nombre. El router la convierte en 409."""


def name_error(name: str) -> str | None:
    """Mensaje legible si el nombre no es válido; None si pasa. Puro."""
    n = (name or "").strip()
    if not n:
        return "El nombre del esquema es obligatorio."
    if not NAME_RE.fullmatch(n):
        return ("Nombre de esquema inválido: letras, números y guión bajo, "
                "empezando con letra (ej. bcp_ddv_clientes).")
    return None


async def list_schemas(project_id: str) -> list[dict]:
    return await repository.list_schemas(project_id)


async def create_schema(project_id: str, body: SchemaBody) -> dict:
    err = name_error(body.name)
    if err:
        raise InvalidSchemaNameError(err)
    name = body.name.strip()
    if await repository.find_by_name(project_id, name):
        raise DuplicateSchemaError(f"Ya existe el esquema {name}")
    return await repository.create_schema(
        {"projectId": project_id, "name": name, "description": body.description, "kind": body.kind})


async def update_schema(sid: str, body: SchemaBody) -> dict | None:
    """PATCH directo (modo sin changeset): NO propaga el rename a tablas/vistas
    — la propagación vive en el flujo versionado (/api/changesets/…/rename)."""
    err = name_error(body.name)
    if err:
        raise InvalidSchemaNameError(err)
    cur = await repository.get_schema(sid)
    if cur is None:
        return None
    name = body.name.strip()
    dup = await repository.find_by_name(cur["projectId"], name)   # unicidad DENTRO del proyecto
    if dup and dup["id"] != sid:
        raise DuplicateSchemaError(f"Ya existe el esquema {name}")
    data: dict = {"name": name, "description": body.description}
    # kind ausente = "no tocar el vigente" (un PATCH de solo nombre/descripción
    # no debe borrar la clasificación ya asignada).
    if body.kind is not None:
        data["kind"] = body.kind
    return await repository.update_schema(sid, data)


async def delete_schema(sid: str) -> bool | str:
    """Soft-delete SOLO si el esquema está vacío en producción.
    Devuelve True (borrado) · False (no existe) · "in-use" (tiene tablas/vistas)."""
    cur = await repository.get_schema(sid)
    if cur is None:
        return False
    if await repository.usage_count(cur["projectId"], cur["name"]):
        return "in-use"
    return await repository.delete_schema(sid)
