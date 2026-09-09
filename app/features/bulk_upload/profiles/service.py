"""Negocio de los perfiles de carga (doc 78 §7.1): validación estructural
contra las defs UDP vivas del proyecto, nombre único CI por proyecto, un solo
default, sugerencias por nombre y materialización del built-in."""
from __future__ import annotations

from pydantic import BaseModel

from app.features.udp import repository as udp_repo

from ..normalize import clean_text, norm_name
from . import repository
from .builtin import BUILTIN_ORIGIN, PLANTILLA_BCP, materialize
from .models import SHEET_ROLES, validate_profile
from .suggest import suggest


class ProfileInvalid(Exception):
    """El perfil tiene problemas estructurales (router → 422 con la lista)."""

    def __init__(self, problems: list[dict]) -> None:
        super().__init__("The profile has problems.")
        self.problems = problems


class ProfileNameTaken(Exception):
    """Ya hay un perfil activo con ese nombre (CI) en el proyecto (router → 409)."""


def _data(body) -> dict:
    return body.model_dump() if isinstance(body, BaseModel) else dict(body)


async def _check(project_id: str, data: dict, *, exclude_id: str | None = None) -> None:
    problems = validate_profile(data, await udp_repo.list_udp(project_id))
    if problems:
        raise ProfileInvalid(problems)
    key = norm_name(data.get("name"))
    for p in await repository.list_profiles(project_id):
        if p["id"] != exclude_id and norm_name(p["name"]) == key:
            raise ProfileNameTaken()


async def list_profiles(project_id: str) -> list[dict]:
    return await repository.list_profiles(project_id)


async def get_profile(project_id: str, profile_id: str) -> dict | None:
    return await repository.get_profile(project_id, profile_id)


async def create_profile(actor: str, project_id: str, body, *, origin: str = "user") -> dict:
    data = {**_data(body), "origin": origin, "createdBy": actor, "updatedBy": actor}
    data["name"] = clean_text(data.get("name"))
    await _check(project_id, data)
    doc = await repository.create_profile(project_id, data)
    if doc.get("isDefault"):
        doc = await repository.set_default(project_id, doc["id"]) or doc
    return doc


async def update_profile(actor: str, project_id: str, profile_id: str, body) -> dict | None:
    current = await repository.get_profile(project_id, profile_id)
    if current is None:
        return None
    data = {**_data(body), "origin": current.get("origin") or "user",
            "createdBy": current.get("createdBy"), "updatedBy": actor}
    data["name"] = clean_text(data.get("name"))
    await _check(project_id, data, exclude_id=profile_id)
    doc = await repository.update_profile(project_id, profile_id, data)
    if doc and doc.get("isDefault"):
        doc = await repository.set_default(project_id, profile_id) or doc
    return doc


async def delete_profile(project_id: str, profile_id: str) -> bool:
    return await repository.delete_profile(project_id, profile_id)


async def set_default(project_id: str, profile_id: str) -> dict | None:
    return await repository.set_default(project_id, profile_id)


async def validate_body(project_id: str, body) -> list[dict]:
    """Los problemas del perfil SIN guardarlo (botón «Check profile»)."""
    return validate_profile(_data(body), await udp_repo.list_udp(project_id))


async def suggest_headers(project_id: str, role: str, headers: list[str]) -> list[dict]:
    if role not in SHEET_ROLES:
        raise ValueError(f"sheet must be one of {SHEET_ROLES}")
    return suggest(role, headers, await udp_repo.list_udp(project_id))


async def create_default(actor: str, project_id: str) -> tuple[dict, list[str]] | None:
    """Materializa el built-in «Plantilla BCP» en el proyecto; None si ya lo
    tiene. Queda como default solo si el proyecto no tenía ninguno (no le roba
    el default a un perfil del usuario)."""
    existing = await repository.list_profiles(project_id)
    if any(p.get("origin") == BUILTIN_ORIGIN for p in existing):
        return None
    body, warnings = materialize(PLANTILLA_BCP, await udp_repo.list_udp(project_id))
    body["isDefault"] = not any(p.get("isDefault") for p in existing)
    doc = await create_profile(actor, project_id, body, origin=BUILTIN_ORIGIN)
    return doc, warnings
