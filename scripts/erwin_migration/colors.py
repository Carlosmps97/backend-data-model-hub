"""Doc 109 — colores de Erwin → colores de la app (puro, sin BD).

En la app un color es una REFERENCIA (`ColorRef`, un string):
  'theme:<id>'  un theme del proyecto (cambia si cambia el theme)
  '#RRGGBB'     un color fijo
  'none'        «sin color» — sólo como excepción de un canvas

Erwin escribe en cada caja su estilo YA resuelto (y marca con `Derived="Y"` lo
heredado). Se parte del color VISIBLE de la caja y se elige la representación
que sigue la estructura de Erwin:

  - el look base de Erwin (theme por defecto del modelo y el blanco «Classic»)
    es el look base de la app: sin color;
  - color elegido a mano en la caja → ese color fijo;
  - heredado de un theme (de la caja, de la tabla, del diagrama, de la subject
    area o del modelo, en ese orden) → referencia a ese theme.

Garantía (test): el color que muestra la app = el color que mostraba Erwin.
"""
from __future__ import annotations

from collections.abc import Iterable

from . import erwin_parser as ep

WHITE = "#FFFFFF"
NONE = "none"


def theme_ref(theme_id: str) -> str:
    return f"theme:{theme_id}"


def neutral_fills(m: ep.ErwinModel) -> set[str]:
    """Rellenos que en Erwin son «sin color»: blanco y el del theme por
    defecto del modelo (el «Default Theme», #CAD8FF en el BCP)."""
    out = {WHITE}
    base = m.themes.get(m.model_theme_ref or "")
    if base:
        out |= {f for f in (base.entity_fill, base.view_fill) if f}
    return out


def colored_themes(m: ep.ErwinModel) -> list[ep.ErwinTheme]:
    """Themes del XML que PINTAN tablas (relleno de entidad no neutro), en el
    orden del archivo — los que se vuelven themes de la app."""
    neutral = neutral_fills(m)
    return [t for t in m.themes.values() if t.name and t.entity_fill and t.entity_fill not in neutral]


def _theme_fill(t: ep.ErwinTheme, kind: str) -> str | None:
    return t.entity_fill if kind == "Entity" else t.view_fill


def _as_app_theme(t: ep.ErwinTheme, kind: str, app_theme: dict[str, str]) -> str | None:
    """Referencia al theme de la app si pinta esta caja con SU color: el theme
    de la app guarda un solo color (el de las tablas), así que una vista sólo lo
    usa si Erwin pinta sus vistas igual que sus tablas. None = color fijo."""
    if t.id not in app_theme or (kind != "Entity" and t.view_fill != t.entity_fill):
        return None
    return theme_ref(app_theme[t.id])


def object_color(m: ep.ErwinModel, obj_theme: str | None, kind: str,
                 app_theme: dict[str, str]) -> str | None:
    """Color propio de la TABLA/VISTA (todos sus canvases): el theme puesto al
    objeto en Erwin. None = sin color. Si ese theme no pudo ser theme de la app
    (mismo nombre con otro color en el proyecto), su color fijo."""
    t = m.themes.get(obj_theme or "")
    if t is None:
        return None
    fill = _theme_fill(t, kind)
    if not fill or fill in neutral_fills(m):
        return None
    return _as_app_theme(t, kind, app_theme) or fill


def box_color(m: ep.ErwinModel, box: ep.ErwinBox, kind: str, chain: Iterable[str | None],
              app_theme: dict[str, str], neutral: set[str]) -> str | None:
    """Color de la caja en SU diagrama. `chain` = theme de la tabla/vista, del
    diagrama, de la subject area y del modelo (la caja va primero). None = sin
    color. Puro."""
    vis = box.fill
    if not vis or vis in neutral:
        return None
    if box.fill_explicit:
        return vis
    for ref in (box.theme_ref, *chain):
        t = m.themes.get(ref or "")
        if t is None:
            continue
        # el primer theme de la cadena es el que aplica; si su color no es el
        # visible (dato raro), manda el visible
        if _theme_fill(t, kind) == vis:
            return _as_app_theme(t, kind, app_theme) or vis
        break
    return vis


def canvas_override(box: str | None, own: str | None) -> str | None:
    """Excepción del canvas para una caja: lo que hay que guardar en
    `subject_areas.colors` (o None si la caja ya se ve con el color de su
    tabla). Una caja sin color sobre una tabla con color guarda 'none'."""
    if box == own:
        return None
    return box if box is not None else NONE
