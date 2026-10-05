"""Doc 109 — textos y dibujos de Erwin → dibujos del canvas de la app (puro).

Un dibujo de la app (`subject_areas.drawings[]`):
  {id, type, x, y, w, h, text?, color?, fill?, font?, align?, valign?}
  - `type`: 'text' (texto de Erwin), 'rect' (rectángulo / título) o 'line'
  - `color`: borde (o la línea); `fill`: relleno; `font`: {family, size (pt),
    bold, italic, underline, color}; `align`/`valign`: posición del texto.

Geometría: centro y tamaño de Erwin × `layout.SCALE` (los tamaños de letra NO
se escalan: quedan en puntos, tal cual). El look base de Erwin (relleno del
theme por defecto) es el blanco de la app.
"""
from __future__ import annotations

from . import erwin_parser as ep
from . import layout as lay

WHITE = "#FFFFFF"
# Tipo de dibujo de Erwin (`Shape.Type`) → tipo de la app. 0 = rectángulo,
# 1 = rectángulo de título (los dos se dibujan como rectángulo), 15 = línea.
LINE_KINDS = {"15"}


def _fill(fill: str | None, neutral: set[str]) -> str:
    return WHITE if not fill or fill in neutral else fill


def _font(s: ep.ErwinTextStyle) -> dict:
    return {"family": s.font or None, "size": round(s.size, 1), "bold": s.bold,
            "italic": s.italic, "underline": s.underline, "color": s.color}


def _rect_fields(rect: list[float]) -> dict:
    return {"x": int(round(rect[0])) or 0, "y": int(round(rect[1])) or 0,
            "w": max(8, int(round(rect[2]))), "h": max(8, int(round(rect[3])))}


def text_drawing(did: str, rect: list[float], text: str, style: ep.ErwinTextStyle,
                 neutral: set[str]) -> dict:
    """Un texto de Erwin (Annotation) en su caja de un diagrama."""
    return {"id": did, "type": "text", **_rect_fields(rect), "text": text,
            "color": style.outline, "fill": _fill(style.fill, neutral), "font": _font(style),
            "align": style.align, "valign": style.valign}


def line_rect(d: ep.ErwinDrawing) -> list[float] | None:
    """Caja de una línea de Erwin (dos puntos). La línea de la app corre por el
    lado LARGO de su caja: horizontal o vertical."""
    if d.center is None or d.end is None:
        return None
    (x1, y1), (x2, y2) = lay.to_app_point(*d.center), lay.to_app_point(*d.end)
    w, h = abs(x2 - x1), abs(y2 - y1)
    if w >= h:
        return [min(x1, x2), (y1 + y2) / 2 - 4, max(w, 8), 8]
    return [(x1 + x2) / 2 - 4, min(y1, y2), 8, max(h, 8)]


def shape_drawing(did: str, d: ep.ErwinDrawing, neutral: set[str]) -> dict | None:
    """Un dibujo de Erwin (rectángulo, título o línea); None sin geometría."""
    s = d.style
    if d.kind in LINE_KINDS:
        rect = line_rect(d)
        if rect is None:
            return None
        return {"id": did, "type": "line", **_rect_fields(rect), "color": s.line or s.outline}
    if d.center is None or d.size is None:
        return None
    out = {"id": did, "type": "rect", **_rect_fields(lay.scaled_rect(d.center, d.size)),
           "color": s.outline, "fill": _fill(s.fill, neutral)}
    if d.text.strip():
        out.update({"text": d.text, "font": _font(s), "align": s.align, "valign": s.valign})
    return out
