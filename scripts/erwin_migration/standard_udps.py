"""Catálogo FIJO de UDPs de Data Standards (doc 61 ronda 2, owner 2026-08-30).

Las DEFINICIONES ya no se derivan del XML: son este catálogo canónico
(uniformizado — las variantes con typos/espacios/tildes de los XML viejos
convergen vía ALIASES). La migración:
  1) siembra/actualiza SIEMPRE el catálogo completo (el estándar existe aunque
     nadie lo use; "serán fijos de momento");
  2) asocia los VALORES del XML comparando case/espacios-insensitive
     (`norm_enum`, A3) contra los valores permitidos y sus alias → se asigna
     la grafía CANÓNICA; sin coincidencia → NO se escribe la key (rige el
     default de la definición);
  3) defs del XML fuera del catálogo → no se crean (al reporte).

"Tipo de Vista" vive a nivel VIEW (en los XML viejos aparecía como def de
Entity porque Erwin no distingue; ese def de tabla queda fuera del catálogo).
"""
from __future__ import annotations

from .policies import norm_enum

_SI_NO = ["No Definido", "Si", "No"]

# name · level · dataType · defaultValue · allowedValues (grafía CANÓNICA).
FIXED_UDPS: list[dict] = [
    # ── Table ──────────────────────────────────────────────────────────────
    {"name": "Clasificacion del Dato", "level": "table", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": ["No Definido", "No DAC", "DAC"]},
    {"name": "Dominio Principal", "level": "table", "dataType": "string",
     "defaultValue": None, "allowedValues": []},
    {"name": "Estado Cloud", "level": "table", "dataType": "list",
     "defaultValue": "No Definido",
     "allowedValues": ["No Definido", "Migracion", "Exclusiva", "No Migrada"]},
    {"name": "Exclusivo Cloud", "level": "table", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": _SI_NO},
    {"name": "Frecuencia Vacuum", "level": "table", "dataType": "list",
     "defaultValue": None,
     "allowedValues": ["CUSTOM_90 days", "DAILY_15 days", "WEEKLY_30 days",
                       "BIWEEKLY_45 days", "MONTHLY_90 days", "QUARTERLY_180 days",
                       "SEMIYEARLY_365 days", "YEARLY_730 days", "EVENTUAL_90 days"]},
    {"name": "Tabla Cross", "level": "table", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": _SI_NO},
    {"name": "Tipo de Carga", "level": "table", "dataType": "list",
     "defaultValue": "No Definido",
     "allowedValues": ["No Definido", "Tipo 1 (No Historia)", "Tipo 2 (Historia Vigencia)",
                       "Tipo 1 (Historia Snapshot)", "Tipo 4 (Historia Snapshot)"]},
    {"name": "Tipo de Entidad", "level": "table", "dataType": "list",
     "defaultValue": "No Definido",
     "allowedValues": ["No Definido", "Super-Tipo", "Sub-Tipo", "Asociacion",
                       "Referencia", "Dependiente", "Independiente"]},
    {"name": "Universal", "level": "table", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": _SI_NO},
    # ── Column ─────────────────────────────────────────────────────────────
    {"name": "Atributo Cross", "level": "column", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": _SI_NO},
    {"name": "Campo Cross", "level": "column", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": _SI_NO},
    {"name": "Clasificacion del Dato", "level": "column", "dataType": "list",
     "defaultValue": "No Definido",
     "allowedValues": ["No Definido", "No DAC", "DAC-DOCUMENTO", "DAC-NOMBRE",
                       "DAC-DIRECCION", "DAC-TELEFONO", "DAC-CUENTA", "DAC-TARJETA",
                       "DAC-EMAIL", "DAC-BIOMETRICO", "DAC-IMAGENVOZ", "DAC-FIRMA",
                       "DAC-GLOSADAC", "DAC", "No Sensible"]},
    {"name": "Exclusivo Cloud", "level": "column", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": _SI_NO},
    {"name": "Particion", "level": "column", "dataType": "list",
     "defaultValue": "No Definido",
     "allowedValues": ["No Definido", "PART_01", "PART_02", "PART_03", "NO", "Si"]},
    {"name": "Tabla Referencia", "level": "column", "dataType": "string",
     "defaultValue": None, "allowedValues": []},
    # ── View (doc 61: el ÚNICO UDP inicial de vistas) ──────────────────────
    {"name": "Tipo de Vista", "level": "view", "dataType": "list",
     "defaultValue": "Regular", "allowedValues": ["Regular", "Personalizada"],
     "description": "Regular = generada desde sources; Personalizada = Query SQL custom."},
    # ── Model (canvas) ─────────────────────────────────────────────────────
    {"name": "Database", "level": "canvas", "dataType": "string",
     "defaultValue": None, "allowedValues": []},
]

# Variantes CONOCIDAS de los XML (norm_enum de la variante → grafía canónica).
# norm_enum ya absorbe case/trim/espacios múltiples; acá van solo las que no:
# typos ("Snaptshot"), tildes y guiones con espacios.
ALIASES: dict[tuple[str, str], dict[str, str]] = {
    ("table", "TIPO DE CARGA"): {
        norm_enum("Tipo 4 (Historia Snaptshot)"): "Tipo 4 (Historia Snapshot)",
        norm_enum("Tipo 4(Historia Snaptshot)"): "Tipo 4 (Historia Snapshot)",
        norm_enum("Tipo 4(Historia Snapshot)"): "Tipo 4 (Historia Snapshot)",
        norm_enum("Tipo 1 (Historia Snaptshot)"): "Tipo 1 (Historia Snapshot)",
        norm_enum("Tipo 1(Historia Snaptshot)"): "Tipo 1 (Historia Snapshot)",
        norm_enum("Tipo 1(Historia Snapshot)"): "Tipo 1 (Historia Snapshot)",
        norm_enum("Tipo 2(Historia Vigencia)"): "Tipo 2 (Historia Vigencia)",
        norm_enum("Tipo 1(No Historia)"): "Tipo 1 (No Historia)",
    },
    ("table", "TIPO DE ENTIDAD"): {
        norm_enum("Sub - Tipo"): "Sub-Tipo",
        norm_enum("Sub Tipo"): "Sub-Tipo",
        norm_enum("Sub- Tipo"): "Sub-Tipo",
        norm_enum("Sub -Tipo"): "Sub-Tipo",
        norm_enum("Asociación"): "Asociacion",
        norm_enum("Super - Tipo"): "Super-Tipo",
        norm_enum("Super Tipo"): "Super-Tipo",
    },
}


def fixed_lookup() -> dict[tuple[str, str], dict]:
    """{(level, norm_enum(name)) → def fija}. Para mapear defs del XML por
    nombre case-insensitive y para el lookup del seed/UI."""
    return {(d["level"], norm_enum(d["name"])): d for d in FIXED_UDPS}


def match_value(fixed: dict, raw: str | None) -> str | None:
    """Valor CANÓNICO para un valor crudo del XML, o None (⇒ rige el default,
    la key NO se escribe). string → texto tal cual (trim); list → match
    case/espacios-insensitive contra allowedValues, luego ALIASES."""
    text = (raw or "").strip()
    if not text:
        return None
    if fixed["dataType"] != "list":
        return text
    key = norm_enum(text)
    for v in fixed["allowedValues"]:
        if norm_enum(v) == key:
            return v
    alias = ALIASES.get((fixed["level"], norm_enum(fixed["name"])), {})
    return alias.get(key)
