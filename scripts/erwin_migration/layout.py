"""Doc 109 — posiciones de Erwin en la app (puro, sin BD).

Erwin guarda el CENTRO de cada caja (`Anchor_Point`, eje Y hacia arriba) y el
tamaño sólo de las cajas que el modelador estiró a mano. La app dibuja cada
bloque con su propia métrica (`table_box`/`view_box`: la misma del canvas
compacto, del export y del auto-arrange) y sus filas miden 28 px, donde Erwin
dibuja ~16.4 unidades. De ahí la regla:

  1. ESCALA fija entre los dos dibujos: `SCALE = 28 / 16.4` (≈ 1.71). Todo —
     centros de bloques, textos y cuadros — se agranda con ese factor, así la
     disposición del modelador se conserva tal cual, sólo más grande.
  2. SEPARACIÓN mínima: los bloques de la app son más anchos/altos que los de
     Erwin en otra proporción, así que algunos quedan pisándose. Se empuja cada
     par que se toca, lo justo, por el eje en el que menos se pisan (aire
     `GAP`). Lo que ya estaba en el canvas (`pinned`) no se mueve.
  3. Los MARCOS (rectángulos que encerraban bloques en Erwin) crecen lo justo
     para seguir encerrándolos después de separar.

No depende de los datos: con cualquier XML el resultado no tiene solapes.
Determinista (mismo XML → mismas posiciones), para que las re-corridas no
muevan nada.
"""
from __future__ import annotations

from collections.abc import Iterable

# Métrica del bloque de la app (`.wk-node`; espejo de `lodSize.ts` y
# `exportScene.ts` del front): ancho por caracteres acotado, alto por filas.
APP_HEADER_H = 32
APP_ROW_H = 28
MIN_W, MAX_W = 240, 880
CHAR_W, PAD_W = 8, 72

# Alto de una fila de atributos en Erwin (unidades de diagrama).
ERWIN_ROW_H = 16.4
SCALE = APP_ROW_H / ERWIN_ROW_H

# Aire mínimo entre dos bloques después de separar.
GAP = 24
_MAX_ROUNDS = 400

# Un marco con texto deja arriba la franja de su título.
FRAME_PAD = 24


def box_width(chars: int) -> int:
    """Ancho del bloque para `chars` caracteres en su fila más larga."""
    return min(MAX_W, max(MIN_W, round(chars * CHAR_W + PAD_W)))


def collapse_complex_type(dtype: str) -> str:
    """Tipo como lo muestra el bloque: un complejo se pliega a `ARRAY<…>`.
    Espejo de `collapseComplexType` (`complexTypes.ts`). Puro."""
    t = (dtype or "").strip()
    lt = t.find("<")
    return t if lt == -1 else f"{t[:lt].strip().upper()}<…>"


def table_box(schema: str, physical: str, logical: str,
              columns: Iterable[tuple[str, str, str]]) -> tuple[int, int]:
    """(ancho, alto) del bloque de una TABLA en la app. `columns` =
    (físico, lógico, tipo). Toma el máximo entre los nombres físicos y lógicos:
    el mismo layout sirve en las dos vistas del canvas sin solaparse. Espejo
    de `tableLodSize` (`lodSize.ts`)."""
    name = max(physical or "", logical or "", key=len)
    header = len(f"{schema}.{name}") if schema else len(name)
    n, maxcol = 0, 12
    for phys, log, dtype in columns:
        n += 1
        maxcol = max(maxcol, max(len(phys or ""), len(log or "")) + len(collapse_complex_type(dtype)))
    return box_width(max(header, maxcol + 3)), APP_HEADER_H + max(1, n) * APP_ROW_H


def view_box(schema: str, name: str, rows: Iterable[tuple[str, str]]) -> tuple[int, int]:
    """(ancho, alto) del bloque de una VISTA: header + una fila por columna de
    salida, `rows` = (nombre, tipo) — el tipo es el `castType` o el de la
    columna de origen, como lo muestra el bloque. Espejo de `viewLodSize`
    (`lodSize.ts`)."""
    header = len(f"{schema}.{name}") if schema else len(name or "")
    n, maxrow = 0, 12
    for row_name, dtype in rows:
        n += 1
        maxrow = max(maxrow, len(row_name or "") + len(collapse_complex_type(dtype)))
    return box_width(max(header, maxrow + 6)), APP_HEADER_H + max(1, n) * APP_ROW_H


def view_rows(sources: Iterable[dict], col_types: dict[tuple[str, str], str],
              first_table: str = "") -> list[tuple[str, str]]:
    """Filas (nombre, tipo) del bloque de una vista GUARDADA, como `viewNodeRows`
    (`viewGraph.ts`): las fuentes con columna o expresión; nombre = alias o
    columna; tipo = `castType` o el de la columna de origen (`col_types` por
    (tableId, FÍSICO EN MAYÚSCULAS); una fuente sin tabla usa la primera de la
    vista, `first_table`). Puro."""
    rows = []
    for src in sources:
        if not (src.get("column") or src.get("expression")):
            continue
        name = (src.get("outputAlias") or "").strip() or src.get("column") or src.get("expression") or ""
        dtype = ((src.get("castType") or "").strip()
                 or col_types.get((src.get("tableId") or first_table, (src.get("column") or "").upper()), ""))
        rows.append((name, dtype))
    return rows


# Alto de la caja de una tabla en Erwin ≈ 34 + 16.4 · filas (auto-ajustada).
ERWIN_HEADER_H = 34


def app_rows(app_h: float) -> int:
    """Filas de un bloque de la app de alto `app_h`. Puro."""
    return max(1, round((app_h - APP_HEADER_H) / APP_ROW_H))


def erwin_height(rows: int) -> float:
    """Alto (en px de la app) de un bloque de `rows` filas en Erwin: con él un
    marco decide si lo ENCIERRA (le cabe a lo alto). Puro."""
    return (ERWIN_HEADER_H + ERWIN_ROW_H * max(1, rows)) * SCALE


def to_app_point(x: float, y: float) -> tuple[float, float]:
    """Punto de Erwin (Y hacia arriba) → punto de la app (Y hacia abajo), escalado."""
    return x * SCALE, -y * SCALE


def app_rect(center: tuple[float, float], size: tuple[float, float]) -> list[float]:
    """[x, y, w, h] de una caja de la app con `size` (YA en px de la app)
    centrada donde Erwin tenía su centro."""
    cx, cy = to_app_point(*center)
    return [cx - size[0] / 2, cy - size[1] / 2, float(size[0]), float(size[1])]


def scaled_rect(center: tuple[float, float], size: tuple[float, float]) -> list[float]:
    """[x, y, w, h] de un texto o cuadro: su tamaño de Erwin también escala."""
    return app_rect(center, (size[0] * SCALE, size[1] * SCALE))


def _clash(a: list[float], b: list[float], gap: float) -> bool:
    return not (a[0] + a[2] + gap <= b[0] or b[0] + b[2] + gap <= a[0]
                or a[1] + a[3] + gap <= b[1] or b[1] + b[3] + gap <= a[1])


def overlaps(rects: dict[str, list[float]], gap: float = 0) -> int:
    """Pares de cajas que se tocan con menos de `gap` de aire. Puro."""
    ids = sorted(rects, key=lambda i: (rects[i][0], i))
    n = 0
    for k, i in enumerate(ids):
        a = rects[i]
        for j in ids[k + 1:]:
            b = rects[j]
            if b[0] >= a[0] + a[2] + gap:
                break
            if _clash(a, b, gap):
                n += 1
    return n


def separate(rects: dict[str, list[float]], pinned: Iterable[str] = (),
             gap: float = GAP, set_aside: list[str] | None = None) -> dict[str, list[float]]:
    """Separa las cajas que se tocan (aire `gap`), moviendo lo MÍNIMO: cada par
    se empuja por el eje de menor penetración, mitad y mitad; si una está fija
    (`pinned`), la otra se mueve entera. Barrido por X para no comparar todos
    contra todos. Devuelve copias (no muta la entrada); determinista.

    Si tras `_MAX_ROUNDS` vueltas algo sigue pisándose (cajas apiladas en un
    mismo punto, columnas muy densas — no pasa con los XML reales), esas cajas
    se ubican APARTE, en filas debajo de todo (`_set_aside`): el resultado nunca
    tiene solapes. Sus ids se agregan a `set_aside` (para el reporte)."""
    out = {k: list(v) for k, v in rects.items()}
    fixed = set(pinned)
    _push_apart(out, fixed, gap)
    stuck = _stuck(out, fixed, gap)
    if stuck:
        _set_aside(out, stuck, gap)
        if set_aside is not None:
            set_aside.extend(stuck)
    return out


def _push_apart(out: dict[str, list[float]], fixed: set[str], gap: float) -> None:
    for _ in range(_MAX_ROUNDS):
        moved = False
        ids = sorted(out, key=lambda i: (out[i][0], i))
        for k, i in enumerate(ids):
            for j in ids[k + 1:]:
                a, b = out[i], out[j]
                if b[0] > a[0] + a[2] + gap:
                    break
                if not _clash(a, b, gap):
                    continue
                if i in fixed and j in fixed:
                    continue
                ox = min(a[0] + a[2] + gap - b[0], b[0] + b[2] + gap - a[0])
                oy = min(a[1] + a[3] + gap - b[1], b[1] + b[3] + gap - a[1])
                axis = 0 if ox <= oy else 1
                push = (ox if axis == 0 else oy) + 0.5
                # el de menor centro en el eje va hacia atrás, el otro hacia adelante
                a_first = a[axis] + a[axis + 2] / 2 <= b[axis] + b[axis + 2] / 2
                sa, sb = (-1, 1) if a_first else (1, -1)
                if i in fixed:
                    b[axis] += sb * push
                elif j in fixed:
                    a[axis] += sa * push
                else:
                    a[axis] += sa * push / 2
                    b[axis] += sb * push / 2
                moved = True
        if not moved:
            break


def _stuck(out: dict[str, list[float]], fixed: set[str], gap: float) -> list[str]:
    """Cajas movibles que todavía se pisan con otra, en orden estable."""
    ids = sorted(out, key=lambda i: (out[i][0], i))
    bad: set[str] = set()
    for k, i in enumerate(ids):
        a = out[i]
        for j in ids[k + 1:]:
            b = out[j]
            if b[0] > a[0] + a[2] + gap:
                break
            if _clash(a, b, gap) and not (i in fixed and j in fixed):
                bad.update(x for x in (i, j) if x not in fixed)
    return sorted(bad)


def _set_aside(out: dict[str, list[float]], ids: list[str], gap: float) -> None:
    """Ubica `ids` en filas debajo de TODO lo demás (como estantes: de izquierda
    a derecha y, al llenar el ancho, una fila nueva). Ninguna se pisa con nada."""
    rest = [r for k, r in out.items() if k not in set(ids)]
    left = min((r[0] for r in rest), default=0.0)
    width = max(4000.0, max((r[0] + r[2] for r in rest), default=0.0) - left)
    x, y = left, max((r[1] + r[3] for r in rest), default=0.0) + 4 * gap
    row_h = 0.0
    for k in ids:
        w, h = out[k][2], out[k][3]
        if x > left and x + w > left + width:
            x, y, row_h = left, y + row_h + gap, 0.0
        out[k][0], out[k][1] = x, y
        x += w + gap
        row_h = max(row_h, h)


def place_beside(rects: dict[str, list[float]], fixed: dict[str, list[float]],
                 gap: float = 200) -> dict[str, list[float]]:
    """Traslada el grupo `rects` entero a la DERECHA de `fixed` (alineado
    arriba), conservando su forma: en una fusión de canvases, el diagrama que
    llega no cae encima del que ya estaba. Sin `fixed` o sin `rects`, igual."""
    if not rects or not fixed:
        return {k: list(v) for k, v in rects.items()}
    right = max(r[0] + r[2] for r in fixed.values())
    top = min(r[1] for r in fixed.values())
    left = min(r[0] for r in rects.values())
    up = min(r[1] for r in rects.values())
    dx, dy = right + gap - left, top - up
    return {k: [r[0] + dx, r[1] + dy, r[2], r[3]] for k, r in rects.items()}


def inside(point: tuple[float, float], rect: list[float]) -> bool:
    return rect[0] <= point[0] <= rect[0] + rect[2] and rect[1] <= point[1] <= rect[1] + rect[3]


def grow_frame(frame: list[float], members: Iterable[list[float]],
               pad: float = FRAME_PAD) -> list[float]:
    """El marco agrandado lo justo para encerrar `members`: sólo por el lado
    donde un bloque SE SALE, y hasta su borde más `pad` de aire. Nunca se
    achica; si ya los encierra queda igual."""
    x0, y0, x1, y1 = frame[0], frame[1], frame[0] + frame[2], frame[1] + frame[3]
    for m in members:
        if m[0] < x0:
            x0 = m[0] - pad
        if m[1] < y0:
            y0 = m[1] - pad
        if m[0] + m[2] > x1:
            x1 = m[0] + m[2] + pad
        if m[1] + m[3] > y1:
            y1 = m[1] + m[3] + pad
    return [x0, y0, x1 - x0, y1 - y0]


def whole(rect: list[float]) -> dict[str, int]:
    """Posición de la esquina en píxeles ENTEROS (doc 100 P6), sin -0."""
    return {"x": int(round(rect[0])) or 0, "y": int(round(rect[1])) or 0}
