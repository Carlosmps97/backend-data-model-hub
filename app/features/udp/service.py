"""Negocio de `udp`. La edición de definiciones es VERSIONADA: pasa por
`data_standards.apply` (kind='udp'), como Glossary/Parent Domains. Este service
solo expone la lectura; las mutaciones las orquesta `data_standards`."""
from __future__ import annotations

from . import repository


async def list_udp() -> list[dict]:
    return await repository.list_udp()
