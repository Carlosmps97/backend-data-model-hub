"""Doc 105 (ronda 6, R16/B2) — el perfil de carga valida el default de un
mapeo de UDP por el TIPO de cada UDP al guardarse (422 con el problema): antes
se guardaba cualquier texto y fallaba recién en cada fila nueva de la carga.
Misma regla que la celda (`bulk_upload.standards.udp_value`). Puro."""
from __future__ import annotations

import copy

import pytest

from app.features.bulk_upload.profiles.models import validate_profile
from tests.features.bulk_upload.test_profiles_model import DEFS, GOOD

TYPED = DEFS + [
    {"id": "c-b", "name": "Activo", "level": "column", "view": "physical", "dataType": "boolean"},
    {"id": "c-n", "name": "Peso", "level": "column", "view": "physical", "dataType": "number"},
    {"id": "c-d", "name": "Alta", "level": "column", "view": "physical", "dataType": "date"},
    {"id": "c-l", "name": "Nivel", "level": "column", "view": "physical", "dataType": "list",
     "allowedValues": ["Alto", "Bajo"]},
    {"id": "c-s", "name": "Nota", "level": "column", "view": "physical", "dataType": "string"},
]


def _profile(udp_id: str, default) -> dict:
    p = copy.deepcopy(GOOD)
    p["sheets"]["columns"]["mappings"].append(
        {"header": "UDP_X", "target": {"kind": "udp", "udpIds": [udp_id]}, "defaultValue": default})
    return p


@pytest.mark.parametrize("udp_id, default", [("c-b", "quizás"), ("c-n", "nan"), ("c-d", "31/12/2026"),
                                             ("c-l", "Medio")])
def test_default_invalido_del_perfil_es_un_problema(udp_id, default):
    problems = validate_profile(_profile(udp_id, default), TYPED)
    (pr,) = [p for p in problems if p["code"] == "default-invalid"]
    assert pr["path"].endswith(".defaultValue") and default in pr["message"] and "upload profile" in pr["message"]


@pytest.mark.parametrize("udp_id, default", [("c-b", "Sí"), ("c-n", "1e3"), ("c-d", "2026-12-31 10:00"),
                                             ("c-l", "alto"), ("c-s", "cualquier texto"), ("c-b", ""), ("c-b", None)])
def test_default_valido_o_vacio_no_es_problema(udp_id, default):
    assert validate_profile(_profile(udp_id, default), TYPED) == []
