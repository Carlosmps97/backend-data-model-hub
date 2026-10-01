"""Compiler: `QuerySpec` → pipeline de Mongo. PURO (sin DB) y testeable.

Valida cada campo/op contra el Field Catalog, castea el value al tipo, y aplica
el PLANNER de escala: un orden por un campo sin índice se RECHAZA (a escala
sería full-scan); `contains/startsWith` usan `re.escape` (nunca
regex arbitrario). El cliente manda keys públicas, jamás paths de Mongo.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field as dc_field

from app.core.udp_values import UDP_FALSE_PATTERN, UDP_TRUE_PATTERN, canonical_number

from .schema import FieldDef
from .spec import Aggregation, Condition, OrderBy, QuerySpec, WhereGroup


class QueryError(Exception):
    """Query inválida (campo/op/valor). El router la mapea a 400/422."""
    def __init__(self, message: str, code: int = 422):
        super().__init__(message)
        self.code = code


@dataclass
class Compiled:
    match: dict
    group: dict | None            # etapa $group (None si no agrupa)
    project: dict                 # $project final (levanta _id / select)
    sort: list[tuple[str, int]]   # [(mongoPath, 1|-1)]
    select: list[FieldDef]        # campos a devolver (para hidratar + columnas de salida)
    grouped: bool
    warnings: list[str] = dc_field(default_factory=list)
    # Agrupado: clave interna del pipeline (g0, a0…) → nombre PÚBLICO de salida.
    output: dict[str, str] = dc_field(default_factory=dict)
    # Agrupado: columnas del resultado, en orden (doc 105, ronda 5).
    columns: list[str] = dc_field(default_factory=list)


def _field(catalog: dict[str, FieldDef], key: str) -> FieldDef:
    fd = catalog.get(key)
    if fd is None:
        raise QueryError(f"Unknown field: {key!r}", code=400)
    return fd


def field_label(fd: FieldDef) -> str:
    """Nombre de un campo en los mensajes: un UDP por su nombre (`udp."Flag"`),
    no por su key interna `udp.<id>` — en producción, un UUID (doc 105, ronda 5)."""
    return f'udp."{fd.label}"' if fd.udpDefId else fd.key


def _number(fd: FieldDef, value) -> int | float:
    """Valor de un campo `number`: FINITO y dentro del rango de un float. Doc
    105 (P12, revisión): un entero desmedido levantaba OverflowError (500) y
    "NaN"/"Infinity"/`1e999` pasaban — el traductor de Lakebase los escribe
    como literal jsonpath y Postgres los rechaza (500). Ambos → 400."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise QueryError(f"Invalid number for {field_label(fd)}: {value!r}", code=400)
    except OverflowError:
        f = math.inf
    if not math.isfinite(f):
        raise QueryError(f"Invalid number for {field_label(fd)}: it must be a finite number.", code=400)
    return int(f) if f.is_integer() else f


# Forma canónica de un número (texto): un entero EXACTO (sin pasar por float);
# un float sin «.0» sólo si es entero y menor que 2^53; None si no es un número.
# Doc 105 (ronda 6): la misma con la que la carga Excel, Data Standards y el
# panel graban los UDP de número (`app/core/udp_values`).
_canonical_number = canonical_number


def _udp_texts(fd: FieldDef, value) -> list[str]:
    """Textos a buscar en un UDP (se guardan como TEXTO, el que tipeó la UI o
    trajo la carga): el valor TAL COMO SE ESCRIBIÓ y, si es un UDP number, su
    forma canónica (`10.0` busca «10.0» y «10»; `9007199254740993`, exacto).
    Doc 105 (ronda 5): un entero grande pasaba por float y buscaba OTRO número.
    Lo que no es un número finito sigue siendo un 400 (`_number` sólo valida)."""
    if fd.type == "number":
        _number(fd, value)
    written = repr(value) if isinstance(value, float) else str(value)
    texts = [written]
    canonical = _canonical_number(value) if fd.type == "number" else None
    if canonical is not None and canonical not in texts:
        texts.append(canonical)
    return texts


def _udp_text(fd: FieldDef, value) -> str:
    """Un valor de UDP como texto (grafía tal como se escribió). Puro."""
    return _udp_texts(fd, value)[0]


def _coerce(fd: FieldDef, value):
    if value is None:
        return None
    if fd.udpDefId:                      # udpValues SIEMPRE string en storage
        return _udp_text(fd, value)
    if fd.type == "number":
        return _number(fd, value)
    if fd.type == "boolean":
        return _boolean(fd, value)
    return str(value)


_TRUE = (True, 1, "1", "true", "True", "TRUE")
_FALSE = (False, 0, "0", "false", "False", "FALSE")


def _boolean(fd: FieldDef, value) -> bool:
    """Doc 105 (ronda 3): sólo valores reconocibles como booleano — antes
    cualquier otro (`2`, `'yes'`) se volvía `false` y el filtro devolvía los
    registros contrarios sin avisar."""
    if isinstance(value, (bool, int, float, str)) and value in _TRUE:
        return True
    if isinstance(value, (bool, int, float, str)) and value in _FALSE:
        return False
    raise QueryError(f"Invalid boolean for {field_label(fd)}: {str(value)[:40]!r} (use true or false).", code=400)


# Doc 105 (ronda 4): un UDP booleano se guarda como TEXTO: `= TRUE` buscaba
# «True». Se compara contra las grafías booleanas, sin distinguir mayúsculas.
# Ronda 5: son las MISMAS con las que la carga Excel y Data Standards escriben
# «true»/«false» (`app/core/udp_values`); los valores anteriores (texto libre de
# la UI: «Sí», «yes»…) se siguen reconociendo al filtrar.
_UDP_TRUE = UDP_TRUE_PATTERN
_UDP_FALSE = UDP_FALSE_PATTERN


def _udp_truth(fd: FieldDef, value) -> bool:
    """¿Verdadero o falso? Acepta las MISMAS grafías que se buscan (`'Sí'`,
    `'yes'`, `'no'`…) además de true/false/1/0 (doc 105, ronda 5: eran 400)."""
    if isinstance(value, str):
        if re.match(_UDP_TRUE, value, re.IGNORECASE):
            return True
        if re.match(_UDP_FALSE, value, re.IGNORECASE):
            return False
    return _boolean(fd, value)
_RANGE_OPS = ("gt", "gte", "lt", "lte", "between")
UDP_NUMBER_TEXT = "UDP numbers are stored as text"


def _bound(fd: FieldDef, op: str, value):
    """Límite de una comparación (`gt`…`between`): exige valor. Doc 105 (P12,
    revisión): sin él llegaba `{"$gt": null}`, que el traductor de Lakebase no
    soporta (500) — y el constructor manda `value` ausente al elegir operador."""
    coerced = _coerce(fd, value)
    if coerced is None:
        raise QueryError(f"The {op} filter on {field_label(fd)} needs a value.", code=400)
    return coerced


def _predicate(fd: FieldDef, op: str, value) -> dict:
    p = fd.path
    if fd.hydrate == "derived":
        raise QueryError(f"Field {field_label(fd)} is calculated and can't be filtered", code=422)
    if fd.udpDefId and fd.type == "number" and op in _RANGE_OPS:
        raise QueryError(f"{field_label(fd)}: {UDP_NUMBER_TEXT} — filter them with =, <> or IN "
                         "(a range would compare text: '10' < '5').", code=422)
    if fd.entity and op not in ("eq", "in"):
        # Vive en otra entidad (schema de columns, en la tabla): el executor lo
        # resuelve a tableIds sólo para = / IN (doc 105: aquí, para `/validate`).
        raise QueryError(f"The {fd.key} filter only supports = and in ({fd.key} comes from the table).",
                         code=422)
    if op not in fd.ops:
        raise QueryError(f"Operator {op!r} isn't allowed for {field_label(fd)} ({fd.type})", code=422)
    if fd.udpDefId and fd.type == "boolean" and op in ("eq", "ne") and value is not None:
        match = {p: {"$regex": _UDP_TRUE if _udp_truth(fd, value) else _UDP_FALSE, "$options": "i"}}
        return match if op == "eq" else {"$nor": [match]}
    if fd.udpDefId and fd.type == "number" and op in ("eq", "ne", "in", "nin"):
        vals = value if op in ("in", "nin") and isinstance(value, list) else [value]
        if op in ("in", "nin") or value is not None:
            # Un null en la lista sigue buscando vacíos (como en los demás campos).
            texts = list(dict.fromkeys(t for v in vals if v is not None for t in _udp_texts(fd, v)))
            if op == "eq":
                return {p: texts[0] if len(texts) == 1 else {"$in": texts}}
            return {p: {"$in" if op == "in" else "$nin": texts + [None] * (None in vals)}}
    if op == "eq":
        return {p: _coerce(fd, value)}
    if op == "ne":
        return {p: {"$ne": _coerce(fd, value)}}
    if op in ("in", "nin"):
        vals = value if isinstance(value, list) else [value]
        return {p: {("$in" if op == "in" else "$nin"): [_coerce(fd, v) for v in vals]}}
    if op == "contains":
        return {p: {"$regex": re.escape(str(value)), "$options": "i"}}
    if op == "startsWith":
        return {p: {"$regex": "^" + re.escape(str(value)), "$options": "i"}}
    if op in ("gt", "gte", "lt", "lte"):
        return {p: {"$" + op: _bound(fd, op, value)}}
    if op == "between":
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise QueryError(f"between needs [min, max] on {field_label(fd)}", code=400)
        return {p: {"$gte": _bound(fd, op, value[0]), "$lte": _bound(fd, op, value[1])}}
    if op == "exists":
        return {p: {"$exists": bool(value) if value is not None else True}}
    if op == "isnull":
        return {"$or": [{p: {"$exists": False}}, {p: {"$in": [None, ""]}}]}
    raise QueryError(f"Unsupported operator: {op}", code=422)  # pragma: no cover


def build_match(node, catalog: dict[str, FieldDef]) -> dict:
    """WhereGroup/Condition → $match. Recursivo. Puro."""
    if node is None:
        return {}
    if isinstance(node, Condition):
        return _predicate(_field(catalog, node.field), node.op, node.value)
    # WhereGroup
    subs = [build_match(c, catalog) for c in node.conditions]
    subs = [s for s in subs if s]
    if not subs:
        return {}
    if node.op == "and":
        return subs[0] if len(subs) == 1 else {"$and": subs}
    if node.op == "or":
        return {"$or": subs}
    return {"$nor": subs}   # not


_AGG = {"count": None, "sum": "$sum", "avg": "$avg", "min": "$min", "max": "$max"}


def check_aggregation_names(group_by: list[str], aggregations: list[Aggregation]) -> None:
    """Doc 105 (ronda 3): el nombre de una agregación es una columna de SALIDA —
    identificador simple (letras, dígitos y `_`, sin empezar con dígito), de a
    lo más 64, distinto de `_id`, sin repetirse ni chocar con un campo del
    groupBy. Era texto libre: `_id`, `$x` o con punto rompían el `$group`
    (500) y un nombre repetido pisaba otra columna. Ronda 5: SIN distinguir
    mayúsculas, como un identificador SQL — `COUNT(*) AS DATATYPE` agrupando
    por `dataType` tapaba al campo en el ORDER BY. Puro."""
    seen = {k.lower() for k in group_by}
    for a in aggregations:
        name = a.as_
        if not name.isidentifier() or name == "_id" or len(name) > 64:
            raise QueryError(f"Invalid aggregation name {name[:40]!r}: use letters, digits and _ "
                             "(up to 64, not starting with a digit, not _id).", code=422)
        if name.lower() in seen:
            raise QueryError(f"The aggregation name {name!r} is repeated: give each aggregation its own name, "
                             "different from the grouped fields.", code=422)
        seen.add(name.lower())


def _accumulator(a: Aggregation, catalog: dict[str, FieldDef]) -> dict:
    if a.fn == "count":
        if a.field:
            # Doc 105 (ronda 3): `count` cuenta FILAS; con un campo se ignoraba.
            raise QueryError("count counts rows and takes no field: use countDistinct to count "
                             "distinct values.", code=422)
        return {"$sum": 1}
    if a.fn == "countDistinct":
        return {"$addToSet": "$" + _field(catalog, a.field).path} if a.field else {"$addToSet": "$_id"}
    if a.field is None:
        raise QueryError(f"Aggregation {a.fn} needs a field", code=400)
    return {_AGG[a.fn]: "$" + _field(catalog, a.field).path}


def _distinct_count(key: str) -> dict:
    """`countDistinct` = valores distintos NO vacíos. Doc 105: como SQL
    (COUNT(DISTINCT x) ignora NULL) y como IS NULL del motor (nulo, ausente o
    texto vacío) — antes el vacío contaba como un valor más. El `$addToSet` los
    junta (en Lakebase entra el `null` guardado, no el ausente): se descuentan
    el nulo y el texto vacío si están. Puro."""
    ref = "$" + key
    return {"$add": [{"$size": ref},
                     {"$cond": [{"$in": [None, ref]}, -1, 0]},
                     {"$cond": [{"$in": ["", ref]}, -1, 0]}]}


def _check_grouping(spec: QuerySpec, catalog: dict[str, FieldDef]) -> None:
    """Qué se puede agrupar y agregar. Doc 105 (ronda 4):
    - un campo de OTRA entidad (schema de columns vive en la tabla) no existe
      en el documento: agrupaba en un único grupo null;
    - SUM/AVG sólo de un campo numérico (de texto daban 0);
    - un UDP number se guarda como TEXTO: SUM/AVG/MIN/MAX compararían texto."""
    fields = [(k, None) for k in spec.groupBy] + [(a.field, a.fn) for a in spec.aggregations if a.field]
    for key, fn in fields:
        fd = _field(catalog, key)
        shown = field_label(fd)
        # Campos derived (calculados post-fetch) no existen en Mongo.
        if fd.hydrate == "derived":
            raise QueryError(f"Field {shown!r} is calculated and can't be grouped or aggregated", code=422)
        if fd.entity:
            raise QueryError(f"Field {shown!r} comes from the table: it can't be grouped or aggregated here.",
                             code=422)
        if fd.udpDefId and fd.type == "number" and fn in ("sum", "avg", "min", "max"):
            raise QueryError(f"{shown}: {UDP_NUMBER_TEXT} — SUM, AVG, MIN and MAX would compare text.",
                             code=422)
        if fn in ("sum", "avg") and (fd.type != "number" or fd.udpDefId):
            raise QueryError(f"SUM and AVG need a numeric field: {shown!r} isn't one.", code=422)


def _grouped_sort(spec: QuerySpec, catalog: dict[str, FieldDef], inner: dict[str, str],
                  project: dict, warnings: list[str]) -> list[tuple[str, int]]:
    """Orden del resultado agrupado: SÓLO por una dimensión o un agregado. Otro
    campo no existe en el grupo y se ignoraba EN SILENCIO (doc 105, ronda 3): se
    sigue ignorando —el Builder conserva el orden de antes de agrupar y un 422
    rompería ese flujo— pero con un aviso (`meta.warnings`). El editor SQL lo
    rechaza (400). Un valor BOOLEANO se ordena por 0/1 (null = vacío): Lakebase
    no ordena booleanos jsonb — true y false empataban. Ronda 4: dimensiones;
    ronda 5: también MIN/MAX de un booleano («tablas con PK primero»)."""
    def boolean(key: str | None) -> bool:
        fd = catalog.get(key) if key else None
        return fd is not None and fd.type == "boolean" and not fd.udpDefId

    aggregations = {a.as_: a for a in spec.aggregations}
    sort: list[tuple[str, int]] = []
    for o in spec.orderBy:
        key = inner.get(o.field)
        if key is None:
            shown = field_label(catalog[o.field]) if o.field in catalog else o.field
            warnings.append(f"Sort by {shown!r} was ignored: in a grouped query sort by a grouped "
                            "field or an aggregation name.")
            continue
        direction = 1 if o.dir == "asc" else -1
        agg = aggregations.get(o.field)
        if o.field in spec.groupBy and boolean(o.field):
            ref = f"$_id.{key}"
        elif agg is not None and agg.fn in ("min", "max") and boolean(agg.field):
            ref = f"${key}"
        else:
            sort.append((key, direction))
            continue
        helper = f"s{len(sort)}"
        project[helper] = {"$cond": [{"$eq": [{"$ifNull": [ref, None]}, None]}, None, {"$cond": [ref, 1, 0]}]}
        sort.append((helper, direction))
    return sort


RESULT_COLUMNS_UNGROUPED = "resultColumns only applies to grouped queries."


def _result_columns(spec: QuerySpec) -> list[str]:
    """Columnas del resultado agrupado, en orden. Doc 105 (ronda 5): las arma el
    editor SQL con su SELECT (agregados antes que dimensiones; una dimensión
    agrupada que no está en el SELECT no sale); sin ellas, groupBy + agregados."""
    names = [*spec.groupBy, *(a.as_ for a in spec.aggregations)]
    if spec.resultColumns is None:
        return names
    wrong = [c for c in spec.resultColumns if c not in names]
    if wrong:
        raise QueryError(f"resultColumns must list grouped fields or aggregation names: {wrong[0]!r} isn't one.",
                         code=422)
    return list(spec.resultColumns)


def compile_spec(spec: QuerySpec, catalog: dict[str, FieldDef]) -> Compiled:
    match = build_match(spec.where, catalog)
    warnings: list[str] = []

    if spec.is_grouped:
        _check_grouping(spec, catalog)
        check_aggregation_names(spec.groupBy, spec.aggregations)
        # Doc 105 (ronda 3): claves INTERNAS en el pipeline (g0…, a0…) y el
        # executor las devuelve con su nombre público. La key de un UDP
        # (`udp.<defId>`) lleva punto: como clave del `_id` y leída como ruta
        # `$_id.udp.<defId>`, la dimensión salía siempre en null.
        inner = {gb: f"g{i}" for i, gb in enumerate(spec.groupBy)}
        inner.update({a.as_: f"a{i}" for i, a in enumerate(spec.aggregations)})
        # _id = dimensiones del groupBy (con $ifNull → null para vacíos).
        gid = {inner[gb]: {"$ifNull": ["$" + _field(catalog, gb).path, None]} for gb in spec.groupBy} or None
        accs, project = {}, {"_id": 0}
        for gb in spec.groupBy:
            project[inner[gb]] = f"$_id.{inner[gb]}"
        for a in spec.aggregations:
            key = inner[a.as_]
            accs[key] = _accumulator(a, catalog)
            if a.fn == "countDistinct":
                project[key] = _distinct_count(key)
            elif a.fn == "sum":
                # Doc 105 (ronda 4): SUM sin ningún valor numérico es NULL (como
                # SQL); `$sum` da 0. Un `$max` oculto dice si hubo alguno.
                accs[key + "n"] = {"$max": "$" + _field(catalog, a.field).path}
                project[key] = {"$cond": [{"$eq": [{"$ifNull": [f"${key}n", None]}, None]}, None, "$" + key]}
            else:
                project[key] = "$" + key
        group = {"_id": gid, **accs}
        sort = _grouped_sort(spec, catalog, inner, project, warnings)
        return Compiled(match=match, group=group, project=project, sort=sort,
                        select=[], grouped=True, warnings=warnings,
                        output={key: name for name, key in inner.items()}, columns=_result_columns(spec))

    if spec.resultColumns is not None:
        raise QueryError(RESULT_COLUMNS_UNGROUPED, code=422)

    # ── Filas (row query) ──
    select_keys = spec.select or [k for k, fd in catalog.items() if fd.udpDefId is None]
    select = [_field(catalog, k) for k in select_keys]
    project = {"_id": 1}
    for fd in select:
        if fd.entity is None:              # cross-entity (p.ej. schema en columns) no se proyecta directo
            project[fd.path] = 1
    # Planner de orden: un row-sort exige índice (sortable) o se rechaza. Y UN
    # solo campo: el keyset pagina por él + `_id` (doc 105: el segundo se ignoraba).
    if len(spec.orderBy) > 1:
        raise QueryError("Sort by one field: rows are paged by a single sort field.", code=422)
    sort: list[tuple[str, int]] = []
    for o in spec.orderBy:
        fd = _field(catalog, o.field)
        if not fd.sortable:
            # Doc 100: se nombran los campos ordenables de ESTA entidad (las
            # relaciones y las vistas no tienen ninguno).
            sortable = [k for k, f in catalog.items() if f.sortable]
            raise QueryError(
                f"Can't sort by {field_label(fd)!r}: it has no index. "
                + (f"Sort by an indexed field: {', '.join(sortable)}." if sortable
                   else "This entity has no sortable field: rows come in their default order."),
                code=422)
        sort.append((fd.path, 1 if o.dir == "asc" else -1))
    return Compiled(match=match, group=None, project=project, sort=sort,
                    select=select, grouped=False, warnings=warnings)
