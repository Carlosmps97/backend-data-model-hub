"""Modelos de `projects` (Project = contenedor padre; Subject Area = módulo/pestaña
que referencia un subconjunto del pool de tablas canónicas + su layout)."""
from __future__ import annotations

import math
import uuid

from pydantic import BaseModel, Field, field_validator

from app.core.colors import clean_colors
from app.core.models import DOC_CONFIG

# Doc 99 — trazos manuales de wires (`SubjectAreaDoc.routes`).
MAX_ROUTE_POINTS = 32          # puntos de quiebre por wire (espejo en el front)
MAX_ROUTE_COORD = 1_000_000    # |coordenada| máxima, en px de canvas
MAX_ROUTES = 5_000             # wires con trazo por canvas (sólo escritura)
MAX_ROUTE_ID_LEN = 200         # largo del id de un wire


class ProjectDoc(BaseModel):
    """Raíz del alcance (doc 75): no lleva `projectId`. Se crea directo; se
    renombra/describe/borra por draft del propio proyecto (D5)."""
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str | None = None


class NodePosDoc(BaseModel):
    model_config = DOC_CONFIG
    x: float
    y: float


def _point_error(point: object) -> str | None:
    """Por qué `point` NO es un punto de quiebre válido, o None si lo es.
    `bool` es `int` en Python: no cuenta como coordenada. Puro."""
    if not isinstance(point, dict):
        return "must be an object with x and y"
    for axis in ("x", "y"):
        v = point.get(axis)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return f"{axis} must be a finite number"
        # `isfinite` SOLO sobre float: con un entero enorme (JSON los admite de
        # cualquier tamaño) lanza OverflowError al convertirlo. Un int siempre
        # es finito; lo desmedido lo corta el rango.
        if isinstance(v, float) and not math.isfinite(v):
            return f"{axis} must be a finite number"
        if abs(v) > MAX_ROUTE_COORD:
            return f"{axis} is out of range (±{MAX_ROUTE_COORD})"
    return None


def _wire_id_ok(wire_id: object) -> bool:
    """Id de wire utilizable como llave: texto no vacío, de largo acotado y sin
    caracteres de control (un `\\u0000` no es representable en JSONB). Puro."""
    return (isinstance(wire_id, str) and bool(wire_id.strip()) and len(wire_id) <= MAX_ROUTE_ID_LEN
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in wire_id))


def routes_error(raw: object) -> str | None:
    """Doc 99 — ESCRITURA estricta de `routes`: mensaje legible si lo que manda
    el cliente no es `{wireId: [{x, y}, …]}` con números finitos, en rango y a
    lo más `MAX_ROUTE_POINTS` puntos por wire (y `MAX_ROUTES` wires por
    canvas); None si es válido. Ausente/None y la lista vacía valen (sin
    trazo). Puro."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return "routes must be an object {wireId: [points]}"
    if len(raw) > MAX_ROUTES:
        return f"routes can have at most {MAX_ROUTES} wires per canvas"
    for wire_id, points in raw.items():
        if not _wire_id_ok(wire_id):
            return (f"routes: invalid wire id (empty, longer than {MAX_ROUTE_ID_LEN} characters "
                    "or with control characters)")
        if not isinstance(points, list):
            return f"routes.{wire_id} must be a list of points"
        if len(points) > MAX_ROUTE_POINTS:
            return f"routes.{wire_id} can have at most {MAX_ROUTE_POINTS} points"
        for i, point in enumerate(points):
            err = _point_error(point)
            if err:
                return f"routes.{wire_id}[{i}] {err}"
    return None


def clean_routes(raw: object) -> dict[str, list[dict]]:
    """Doc 99 — LECTURA tolerante de `routes`: el trazo es decoración, así que
    un dato corrupto jamás puede dejar un canvas (ni el árbol del proyecto, que
    valida todos sus canvases) sin abrir. Un trazo con CUALQUIER punto inválido
    se descarta entero — el wire vuelve al camino automático — y los sanos
    quedan; de cada punto sólo se conservan `x` e `y`. No muta la entrada. Puro."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for wire_id, points in raw.items():
        if not _wire_id_ok(wire_id):
            continue
        if not isinstance(points, list) or not points or len(points) > MAX_ROUTE_POINTS:
            continue
        if any(_point_error(p) for p in points):
            continue
        out[wire_id] = [{"x": p["x"], "y": p["y"]} for p in points]
    return out


class SubjectAreaDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    projectId: str
    # `folderId` ubica el canvas dentro de la jerarquía del Model Explorer
    # (None = raíz del proyecto). `drawings` = capa DRAWING (shapes/text con
    # estilo), un canvas = un diagrama ER. Ambos aditivos (invariante §2.6).
    folderId: str | None = None
    name: str
    tableIds: list[str] = Field(default_factory=list)
    layout: dict[str, NodePosDoc] = Field(default_factory=dict)
    drawings: list[dict] = Field(default_factory=list)
    # F5 — UDP del Modelo de Datos: valores {defId: value} de las definiciones
    # level='canvas'. Aditivo con default (invariante §2.6: declarado acá Y en
    # el TS SubjectArea, o el dato desaparece al recargar).
    udpValues: dict[str, str] = Field(default_factory=dict)
    # Doc 70 §2: membresía EXPLÍCITA de vistas en el canvas (paridad con
    # `tableIds`; la posición ya vivía en `layout[viewId]`). None = canvas
    # LEGACY que nunca materializó su lista: rige la regla vieja del doc 10 D3
    # (`showOnCanvas` ∧ fuentes ∩ tableIds). Lista (aun vacía) = sólo esas
    # vistas. Aditivo (invariante §2.6): declarado acá Y en el TS SubjectArea.
    viewIds: list[str] | None = None
    # Doc 99 — trazos MANUALES de wires: puntos de quiebre por wire, en
    # coordenadas del canvas y en orden padre → hijo. La llave es el id del
    # wire en el canvas: el de la relación (wire crow's-foot y rama de
    # subcategoría) o `subsym-{símbolo}` (tronco supertipo → símbolo). Un wire
    # sin entrada usa el camino automático. Van POR CANVAS (la relación es una
    # sola para todos) y se versionan con el documento, como `layout`. Aditivo
    # (invariante §2.6): declarado acá Y en el TS SubjectArea.
    routes: dict[str, list[NodePosDoc]] = Field(default_factory=dict)
    # Doc 109 — color de las cajas EN ESTE canvas: {id de tabla o vista:
    # ColorRef}. Excepción sobre el color propio de la tabla/vista (`color`);
    # 'none' = sin color aquí. Se versiona con el documento, como `layout`.
    # Aditivo (invariante §2.6): declarado acá Y en el TS SubjectArea.
    colors: dict[str, str] = Field(default_factory=dict)

    _clean_routes = field_validator("routes", mode="before")(clean_routes)
    _clean_colors = field_validator("colors", mode="before")(clean_colors)
