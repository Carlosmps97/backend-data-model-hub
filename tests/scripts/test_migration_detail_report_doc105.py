"""Doc 105 (revisión R2): el kit ahora guarda en `erwinLongId` el diagrama que
CREÓ el canvas y en `erwinLongIds` todos sus aportes (fusión R8). El reporte de
incongruencias ubicaba los canvases sólo por `erwinLongId`: un diagrama del
otro archivo salía «(canvas no encontrado)»."""
from __future__ import annotations

from scripts.migration_detail_report import canvas_by_erwin_id


def test_un_canvas_fusionado_se_ubica_por_cualquiera_de_sus_diagramas():
    sas = {"sa1": {"erwinLongId": "DG1", "erwinLongIds": ["DG1", "DG2"]},
           "sa2": {"erwinLongId": "DG3"},                       # canvas del kit anterior (sin lista)
           "sa3": {"name": "hecho en la app"}}
    assert canvas_by_erwin_id(sas) == {"DG1": "sa1", "DG2": "sa1", "DG3": "sa2"}


def test_prefiere_el_canvas_activo_y_el_creador():
    """Ronda 3 (revisor R5): con `setdefault` en el orden de la BD ganaba el
    primero que apareciera (inactivo, o uno que sólo recibió aportes)."""
    sas = {"viejo": {"erwinLongId": "DG1", "flgactive": False},
           "aporta": {"erwinLongId": "DG9", "erwinLongIds": ["DG9", "DG1"]},
           "creador": {"erwinLongId": "DG1", "erwinLongIds": ["DG1"]}}
    assert canvas_by_erwin_id(sas)["DG1"] == "creador"


def test_una_vista_omitida_por_su_fuente_borrada_en_la_app_lo_dice():
    """Ronda 3: el kit ahora omite las vistas cuya fuente se borró en la app
    (`reason`); el reporte las contaba como «sin tabla fuente resoluble»."""
    from scripts.migration_detail_report import discarded_view_texts

    caso, detalle = discarded_view_texts({"view": "V_X", "reason": "source deleted in the app"})
    assert "borrada" in caso and "revive" in detalle
    caso, _ = discarded_view_texts({"view": "V_Y"})
    assert caso == "Sin tabla fuente resoluble (no migrada)"
