"""`arrange_all` re-organiza canvases COMPLETOS con ELK. Doc 99: al cambiar
todas las posiciones, los trazos manuales de los wires de ese canvas dejan de
calzar con los bloques — el arrange los reinicia en la MISMA escritura (igual
que el botón Autoarrange de la web)."""
from __future__ import annotations

from scripts.arrange_all import layout_updates


def _ops(positions: dict) -> list[tuple[dict, dict]]:
    return [(op._filter, op._doc) for op in layout_updates(positions)]


def test_el_arrange_graba_las_posiciones_y_reinicia_los_trazos():
    positions = {"sa1": {"t1": {"x": 1, "y": 2}, "v1": {"x": 300, "y": 2}}, "sa2": {"t9": {"x": 0, "y": 0}}}
    assert _ops(positions) == [
        ({"_id": "sa1"}, {"$set": {"layout": {"t1": {"x": 1, "y": 2}, "v1": {"x": 300, "y": 2}}, "routes": {}}}),
        ({"_id": "sa2"}, {"$set": {"layout": {"t9": {"x": 0, "y": 0}}, "routes": {}}}),
    ]


def test_sin_canvases_no_hay_escrituras():
    assert _ops({}) == []


def test_solo_toca_los_canvases_que_arreglo():
    """Con `--project`, ELK sólo recibe los canvases de ese proyecto: los demás
    conservan posiciones Y trazos (no aparecen en `positions`)."""
    assert [f["_id"] for f, _ in _ops({"sa-del-proyecto": {"t1": {"x": 1, "y": 1}}})] == ["sa-del-proyecto"]
