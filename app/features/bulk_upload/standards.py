"""Data Standards y naming vistos por el planner (doc 55 §4-5). Puro.

`Standards` indexa las definiciones UDP por nivel y nombre normalizado, los
parent domains por nombre, y resuelve físicos/longitudes con la naming config
del scope. `resolve_udp_headers` cruza las cabeceras `UDP_*` de una hoja con
las definiciones; `apply_udps` calcula el mapa `udpValues` de una entidad.
"""
from __future__ import annotations

from app.core.naming import physicalize

from .context import UploadContext
from .datatypes import canonical_type
from .normalize import clean_text, norm_enum, norm_key, norm_name
from .report import ReportBuilder

_BOOL_TRUE = frozenset({"true", "si", "sí", "yes", "1", "verdadero"})
_BOOL_FALSE = frozenset({"false", "no", "0", "falso"})


class Standards:
    def __init__(self, ctx: UploadContext) -> None:
        self.ctx = ctx
        self._defs: dict[str, dict[str, list[dict]]] = {}
        for d in ctx.udp_defs:
            level = d.get("level") or "column"
            self._defs.setdefault(level, {}).setdefault(norm_key(d.get("name")), []).append(d)
        self._domains: dict[str, list[dict]] = {}
        for d in ctx.domains:
            self._domains.setdefault(norm_name(d.get("name")), []).append(d)
        self._domain_by_id = {d["id"]: d for d in ctx.domains if d.get("id")}
        self.type_extras = [d["defaultDataType"] for d in ctx.domains if d.get("defaultDataType")]

    # ── UDP ────────────────────────────────────────────────────────────────
    def defs_for(self, level: str, header: str) -> list[dict]:
        return self._defs.get(level, {}).get(norm_key(header), [])

    # ── Parent domains ─────────────────────────────────────────────────────
    def domain(self, name) -> dict | None:
        hits = self._domains.get(norm_name(name), [])
        return hits[0] if hits else None

    def domain_default(self, domain_id: str | None) -> str | None:
        d = self._domain_by_id.get(domain_id or "")
        return clean_text(d.get("defaultDataType")) or None if d else None

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
    `allowedValues` con la regla del kit (`norm_enum`); number/boolean se
    validan; string/date van tal cual."""
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
        try:
            float(raw)
        except ValueError:
            return None, f"'{raw}' is not a number (UDP '{name}')."
        return raw, None
    if kind == "boolean":
        low = raw.strip().lower()
        if low in _BOOL_TRUE:
            return "true", None
        if low in _BOOL_FALSE:
            return "false", None
        return None, f"'{raw}' is not a boolean value for UDP '{name}' (use true/false or Si/No)."
    return raw, None


def resolve_udp_headers(headers: list[str], level: str, rows_udp: list[dict[str, str]],
                        std: Standards, rb: ReportBuilder, sheet: str) -> dict[str, dict]:
    """Cabecera `UDP_*` → definición del nivel. Sin definición: error si la
    columna trae algún valor (se perdería data), warning si viene vacía."""
    out: dict[str, dict] = {}
    for h in headers:
        defs = std.defs_for(level, h)
        if len(defs) == 1:
            out[h] = defs[0]
            continue
        if len(defs) > 1:
            names = ", ".join(str(d.get("name")) for d in defs)
            rb.error(sheet, "ambiguous-udp",
                     f"Column '{h}' matches several {level}-level UDP definitions ({names}); "
                     "rename one of them in Data Standards.", column=h)
            continue
        base = f"Column '{h}' doesn't match any {level}-level UDP definition in Data Standards"
        if any(clean_text(r.get(h)) for r in rows_udp):
            rb.error(sheet, "unknown-udp", base + "; its values can't be loaded.", column=h)
        else:
            rb.warning(sheet, "unknown-udp", base + " (empty column, ignored).", column=h)
    return out


def apply_udps(udp_map: dict[str, dict], row_udp: dict[str, str], existing: dict | None,
               rb: ReportBuilder, sheet: str, row: int) -> dict[str, str]:
    """`udpValues` resultante: parte de los valores existentes (update) o de
    nada (alta); celda con valor → validada; celda vacía → default de la
    definición SOLO si la entidad no tenía valor."""
    values = {k: str(v) for k, v in (existing or {}).items() if v is not None}
    for header, d in udp_map.items():
        raw = clean_text(row_udp.get(header))
        if raw:
            val, err = udp_value(d, raw)
            if err:
                rb.error(sheet, "invalid-udp-value", err, row=row, column=header)
            elif val is not None:
                values[d["id"]] = val
        elif d["id"] not in values and clean_text(d.get("defaultValue")):
            values[d["id"]] = clean_text(d.get("defaultValue"))
    return values
