"""Doc 105 (ronda 7, R18b) — un default del perfil de carga que QUEDÓ inválido
(p. ej. Data Standards sacó el valor de la lista después de guardar el perfil)
no bloquea toda la carga.

La ronda 6 validó el default al GUARDAR el perfil (422, sigue así), pero
`validate_workbook` corre la misma validación en CADA carga y ahí todo
problema del perfil era fatal: una carga cuyas filas traen su valor —el
default nunca se usaría— no se podía aplicar. Ahora en la carga es un AVISO;
la fila que deja la celda vacía y lo usaría da su propio error."""
from __future__ import annotations

import asyncio
import copy

from app.features.bulk_upload import service
from app.features.bulk_upload.schemas import RawRow, RawSheet, UploadWorkbookBody
from tests.features.bulk_upload.test_service import PROFILE, _wait, env  # noqa: F401  (fixture)

NIVEL = {"id": "u-nivel", "name": "Nivel", "level": "table", "view": "physical", "dataType": "list",
         "allowedValues": ["Alto", "Bajo"]}          # «Medio» salió de la lista DESPUÉS de guardar el perfil
HEADER = RawRow(row=2, cells=["TABLA_LOGICO", "ESQUEMA", "PROJECT", "DIAGRAMA", "NIVEL"])


def _validate(env, rows: list[RawRow]) -> dict:  # noqa: F811
    profile = copy.deepcopy(PROFILE)
    profile["sheets"]["tables"]["mappings"].append(
        {"header": "NIVEL", "target": {"kind": "udp", "udpIds": ["u-nivel"]}, "defaultValue": "Medio"})
    env["profile"] = profile
    env["ctx"].udp_defs = [NIVEL]
    body = UploadWorkbookBody(fileName="carga.xlsx", profileId="pf",
                              sheets=[RawSheet(name="Tablas", rows=[HEADER, *rows])])

    async def run():
        return await _wait(await service.start_validation("c1", "ana", body))

    return asyncio.run(run())


def test_un_default_del_perfil_que_ninguna_fila_usa_no_bloquea_la_carga(env):  # noqa: F811
    done = _validate(env, [RawRow(row=3, cells=["Tabla A", "ddv", "P", "D", "Alto"]),
                           RawRow(row=4, cells=["Tabla B", "ddv", "P", "D", "Bajo"])])
    report = done["report"]
    assert done["status"] == "validated" and report["errors"] == [], report["errors"]
    (w,) = [w for w in report["warnings"] if w["code"] == "profile-default-invalid"]
    assert (w["sheet"], w["column"]) == ("Profile", "sheets.tables.mappings[4].defaultValue")
    assert "Medio" in w["message"] and "Nivel" in w["message"]


def test_la_fila_que_usaria_el_default_da_su_error(env):  # noqa: F811
    done = _validate(env, [RawRow(row=3, cells=["Tabla A", "ddv", "P", "D", "Alto"]),
                           RawRow(row=4, cells=["Tabla B", "ddv", "P", "D", ""])])
    (e,) = done["report"]["errors"]
    assert (e["code"], e["row"], e["column"]) == ("invalid-udp-value", 4, "NIVEL")
    assert "upload profile" in e["message"] and "Medio" in e["message"]
