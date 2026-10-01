"""Data Standards y naming vistos por el planner (doc 55 §4-5 · doc 78). Puro.

`Standards` indexa los parent domains por nombre y resuelve físicos/longitudes
con la naming config del scope. `apply_udps` calcula el mapa `udpValues` de
una entidad a partir del mapeo del PERFIL (cabecera → defs, doc 78 D2): la
resolución de cabeceras por nombre ya no existe (es explícita en el perfil).
"""
from __future__ import annotations

from app.core.naming import physicalize
from app.core.udp_values import canonical_number, is_finite_number, iso_date, normalize_boolean

from .context import UploadContext
from .datatypes import canonical_type
from .normalize import clean_text, norm_enum, norm_name
from .report import ReportBuilder



class Standards:
    def __init__(self, ctx: UploadContext) -> None:
        self.ctx = ctx
        self._domains: dict[str, list[dict]] = {}
        for d in ctx.domains:
            self._domains.setdefault(norm_name(d.get("name")), []).append(d)
        self._domain_by_id = {d["id"]: d for d in ctx.domains if d.get("id")}
        self.type_extras = [d["defaultDataType"] for d in ctx.domains if d.get("defaultDataType")]

    # ── Parent domains ─────────────────────────────────────────────────────
    def domain(self, name) -> dict | None:
        hits = self._domains.get(norm_name(name), [])
        return hits[0] if hits else None

    def domain_default(self, domain_id: str | None) -> str | None:
        d = self._domain_by_id.get(domain_id or "")
        return clean_text(d.get("defaultDataType")) or None if d else None

    def domain_logical(self, domain_id: str | None) -> str | None:
        """Tipo LÓGICO del dominio (doc 69), None si no lo declara."""
        d = self._domain_by_id.get(domain_id or "")
        return clean_text(d.get("logicalDataType")) or None if d else None

    # ── Naming ─────────────────────────────────────────────────────────────
    def physicalize(self, logical: str, scope: str) -> str:
        cfg = self.ctx.naming.get(scope) or {}
        return physicalize(clean_text(logical), self.ctx.glossary.get(scope) or {},
                           separator=cfg.get("separator", ""), case=cfg.get("case", "upper"))

    def max_len(self, scope: str) -> int:
        return int((self.ctx.naming.get(scope) or {}).get("maxLength") or 0)

    # ── Tipos ──────────────────────────────────────────────────────────────
    def canonical_type(self, text) -> str | None:
        return canonical_type(text, extra=self.type_extras)


def udp_value(defn: dict, raw: str) -> tuple[str | None, str | None]:
    """Valor a persistir (grafía canónica) o mensaje de error. Listas: contra
    `allowedValues` con la regla del kit (`norm_enum`); number: finito y en su
    forma canónica (la del motor del Reporting, ronda 6); date: ISO
    `YYYY-MM-DD` real (de una fecha-hora ISO, sólo la fecha — ronda 6);
    boolean se normaliza; string va tal cual."""
    kind = defn.get("dataType") or "string"
    name = defn.get("name") or "UDP"
    if kind == "list":
        allowed = [str(v) for v in (defn.get("allowedValues") or [])]
        key = norm_enum(raw)
        for v in allowed:
            if norm_enum(v) == key:
                return v, None
        return None, (f"'{raw}' is not an allowed value for UDP '{name}' "
                      f"(allowed: {', '.join(allowed) if allowed else 'none defined'}).")
    if kind == "number":
        # Doc 105: finito — `float()` aceptaba «nan»/«inf» (ronda 5) — y en su
        # forma canónica: «10.50» y «10.5» son el mismo valor (ronda 6).
        if not is_finite_number(raw):
            return None, f"'{raw}' is not a finite number (UDP '{name}')."
        return canonical_number(raw), None
    if kind == "date":
        # Doc 105: el front manda las celdas de fecha en ISO; otra forma de
        # texto es un error de la fila (ronda 5). Con hora, sólo la fecha (ronda 6).
        day = iso_date(raw)
        if day is None:
            return None, f"'{raw}' is not a valid date for UDP '{name}' (use YYYY-MM-DD)."
        return day, None
    if kind == "boolean":
        # Doc 105 (ronda 5): las grafías del motor del Reporting (módulo compartido).
        value = normalize_boolean(raw)
        if value is None:
            return None, f"'{raw}' is not a boolean value for UDP '{name}' (use true/false or Si/No)."
        return value, None
    return raw, None


def profile_default_error(defn: dict, default: str, err: str) -> str:
    """Doc 105 (ronda 6): el error de un default del PERFIL de carga dice de
    dónde viene (la celda estaba vacía) — en la fila y al guardar el perfil."""
    return (f"The default value '{default}' of UDP '{defn.get('name') or 'UDP'}' in the upload profile "
            f"is invalid: {err}")


def definition_default(defn: dict) -> tuple[str | None, str | None]:
    """Default de la DEFINICIÓN a escribir en una entidad sin valor (o error).
    Doc 105 (ronda 5): Data Standards lo guardaba como texto libre; pasa por
    la misma regla que la celda — booleano normalizado («Sí» → «true»), número
    finito y fecha ISO real — y uno inválido es un error de la fila. Texto y
    lista, tal cual."""
    default = clean_text(defn.get("defaultValue"))
    if not default or (defn.get("dataType") or "string") not in ("boolean", "number", "date"):
        return default or None, None
    value, err = udp_value(defn, default)
    return value, (f"The default value of UDP '{defn.get('name') or 'UDP'}' in Data Standards is invalid: {err}"
                   if err else None)


def apply_udps(udp_map: dict[str, list[dict]], row_udp: dict[str, str], existing: dict | None,
               rb: ReportBuilder, sheet: str, row: int, udp_defaults: dict[str, str] | None = None,
               is_new: bool = False) -> dict[str, str]:
    """`udpValues` resultante: parte de los valores existentes (update) o de
    nada (alta). Una cabecera alimenta N defs (doc 78 D2) y la celda se valida
    contra CADA una. Celda vacía: default del mapeo (solo entidad nueva) y, si
    no, default de la definición cuando la entidad no tenía valor."""
    values = {k: str(v) for k, v in (existing or {}).items() if v is not None}
    for header, defs in udp_map.items():
        raw = clean_text(row_udp.get(header))
        from_profile = False
        if not raw and is_new:
            raw = clean_text((udp_defaults or {}).get(header))
            from_profile = bool(raw)
        for d in defs:
            if raw:
                val, err = udp_value(d, raw)
                if err:
                    rb.error(sheet, "invalid-udp-value",
                             profile_default_error(d, raw, err) if from_profile else err, row=row, column=header)
                elif val is not None:
                    values[d["id"]] = val
            elif d["id"] not in values:
                val, err = definition_default(d)
                if err:
                    rb.error(sheet, "invalid-udp-value", err, row=row, column=header)
                elif val is not None:
                    values[d["id"]] = val
    return values
