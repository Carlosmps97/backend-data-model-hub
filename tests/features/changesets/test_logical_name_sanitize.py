"""Doc 92 D8: el choke point de changesets limpia el `logicalName` de tablas y
columnas (todo escritor) — puro, sin I/O."""
from __future__ import annotations

from app.features.changesets.service import sanitize_logical_names


def test_limpia_tablas_y_columnas_y_devuelve_los_tocados():
    items = [
        {"collection": "canonical_tables", "op": "upsert", "entityId": "t1",
         "payload": {"logicalName": "Cuenta (activa)", "physicalName": "M_CUENTA"}},
        {"collection": "canonical_columns", "op": "upsert", "entityId": "c1",
         "payload": {"logicalName": "Código/Cliente", "physicalName": "CODCLI"}},
        {"collection": "canonical_columns", "op": "upsert", "entityId": "c2",
         "payload": {"logicalName": "Nombre Cliente", "physicalName": "NBRCLI"}},
    ]
    touched = sanitize_logical_names(items)
    assert [t["entityId"] for t in touched] == ["t1", "c1"]
    assert items[0]["payload"]["logicalName"] == "Cuenta activa"
    assert items[1]["payload"]["logicalName"] == "CódigoCliente"
    assert items[2]["payload"]["logicalName"] == "Nombre Cliente"
    # el físico no se toca
    assert items[0]["payload"]["physicalName"] == "M_CUENTA"


def test_ignora_deletes_otras_colecciones_y_payloads_sin_logico():
    items = [
        {"collection": "canonical_tables", "op": "delete", "entityId": "t1", "payload": None},
        {"collection": "views", "op": "upsert", "entityId": "v1", "payload": {"name": "V (x)", "logicalName": "a/b"}},
        {"collection": "canonical_tables", "op": "upsert", "entityId": "t2", "payload": {"physicalName": "X"}},
        {"collection": "canonical_columns", "op": "upsert", "entityId": "c9", "payload": {"logicalName": None}},
    ]
    assert sanitize_logical_names(items) == []
    assert items[1]["payload"]["logicalName"] == "a/b"
