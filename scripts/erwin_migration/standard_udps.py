"""Catálogo FIJO de UDPs de Data Standards (doc 61 r2; revisado por el owner
en el doc 68, imágenes 2026-09-03; doc 69, imágenes 2026-09-05 — FACETAS).

Cada definición declara su faceta Erwin (`view`): 'logical' = Entity /
Attribute, 'physical' = Table / Column / View / Model. Las defs homónimas de
ambas facetas («Clasificacion del Dato») son definiciones DISTINTAS con ids
propios (ver `migrate.py`: las físicas conservan el id histórico
`udpfix|level|name`; las lógicas usan `udpfix|level|logical|name`).
25 definiciones: Entity·Logical 6 · Table·Physical 8 · Attribute·Logical 2 ·
Column·Physical 5 · View 2 · Model 2. Default de lista = PRIMER valor.

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

"Tipo de Vista" vive a nivel VIEW (físico) y, por fidelidad al estándar
entregado por los modeladores (doc 69 §5.1), también como UDP de ENTIDAD en
la faceta lógica. A nivel tabla FÍSICO no existe.
"""
from __future__ import annotations

from .policies import norm_enum

_SI_NO = ["No Definido", "Si", "No"]
_DAC_COLUMNA = ["No Definido", "No DAC", "DAC-DOCUMENTO", "DAC-NOMBRE", "DAC-DIRECCION",
                "DAC-TELEFONO", "DAC-CUENTA", "DAC-TARJETA", "DAC-EMAIL", "DAC-BIOMETRICO",
                "DAC-IMAGENVOZ", "DAC-FIRMA", "DAC-GLOSADAC", "DAC"]
_TIPO_ENTIDAD = ["No Definido", "Super-Tipo", "Sub-Tipo", "Asociacion", "Referencia", "Dependiente"]

# name · level · view · dataType · defaultValue · allowedValues (grafía CANÓNICA).
FIXED_UDPS: list[dict] = [
    # ── Table · faceta FÍSICA (doc 68, imagen owner 2026-09-03) ────────────
    {"name": "Clasificacion del Dato", "level": "table", "view": "physical", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": ["No Definido", "No DAC", "DAC"]},
    {"name": "Dominio Principal", "level": "table", "view": "physical", "dataType": "string",
     "defaultValue": None, "allowedValues": []},
    {"name": "Estado Cloud", "level": "table", "view": "physical", "dataType": "list",
     "defaultValue": "No Definido",
     "allowedValues": ["No Definido", "Migracion", "Exclusiva", "No Migrada"]},
    # Doc 68 (imágenes owner 2026-09-03): «Exclusivo Cloud» dejó de ser de
    # tabla (solo existe a nivel columna); default de lista = SIEMPRE el
    # primer valor.
    {"name": "Frecuencia Vacuum", "level": "table", "view": "physical", "dataType": "list",
     "defaultValue": "CUSTOM_90 days",
     "allowedValues": ["CUSTOM_90 days", "DAILY_15 days", "WEEKLY_30 days",
                       "BIWEEKLY_45 days", "MONTHLY_90 days", "QUARTERLY_180 days",
                       "SEMIYEARLY_365 days", "YEARLY_730 days", "EVENTUAL_90 days"]},
    {"name": "Tabla Cross", "level": "table", "view": "physical", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": _SI_NO},
    # Grafía canónica = «Snapshot» BIEN ESCRITO (owner 2026-09-09). Deroga la
    # decisión 1 del doc 68, que había adoptado el typo «Snaptshot» del estándar
    # corporativo por round-trip fiel: el typo pasa a ALIASES, así los XML que
    # lo traen siguen convergiendo. Fuera del catálogo: «Tipo 1 (Historia
    # Snapshot)» e «Independiente» (no están en la imagen del estándar).
    {"name": "Tipo de Carga", "level": "table", "view": "physical", "dataType": "list",
     "defaultValue": "No Definido",
     "allowedValues": ["No Definido", "Tipo 1 (No Historia)", "Tipo 2 (Historia Vigencia)",
                       "Tipo 4 (Historia Snapshot)"]},
    {"name": "Tipo de Entidad", "level": "table", "view": "physical", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": _TIPO_ENTIDAD},
    {"name": "Universal", "level": "table", "view": "physical", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": _SI_NO},
    # ── Entity · faceta LÓGICA (doc 69, imagen owner 2026-09-05) ──────────
    {"name": "Filtro Despliegue 2021", "level": "table", "view": "logical", "dataType": "string",
     "defaultValue": "NO", "allowedValues": []},
    {"name": "Tipo de Vista", "level": "table", "view": "logical", "dataType": "list",
     "defaultValue": "Regular", "allowedValues": ["Regular", "Personalizada"]},
    {"name": "Clasificacion del Dato", "level": "table", "view": "logical", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": ["No Definido", "No DAC", "DAC"]},
    {"name": "Universal", "level": "table", "view": "logical", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": _SI_NO},
    {"name": "Tipo de Entidad", "level": "table", "view": "logical", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": _TIPO_ENTIDAD},
    {"name": "Dominio Principal", "level": "table", "view": "logical", "dataType": "string",
     "defaultValue": None, "allowedValues": []},
    # ── Column · faceta FÍSICA ─────────────────────────────────────────────
    # Doc 68: «Atributo Cross» es de la faceta LÓGICA (Attribute), no de columna.
    {"name": "Campo Cross", "level": "column", "view": "physical", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": _SI_NO},
    {"name": "Clasificacion del Dato", "level": "column", "view": "physical", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": _DAC_COLUMNA},
    {"name": "Exclusivo Cloud", "level": "column", "view": "physical", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": _SI_NO},
    {"name": "Particion", "level": "column", "view": "physical", "dataType": "list",
     "defaultValue": "No Definido",
     "allowedValues": ["No Definido", "PART_01", "PART_02", "PART_03"]},
    {"name": "Tabla Referencia", "level": "column", "view": "physical", "dataType": "string",
     "defaultValue": None, "allowedValues": []},
    # ── Attribute · faceta LÓGICA (doc 69) ────────────────────────────────
    {"name": "Clasificacion del Dato", "level": "column", "view": "logical", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": _DAC_COLUMNA},
    {"name": "Atributo Cross", "level": "column", "view": "logical", "dataType": "list",
     "defaultValue": "No Definido", "allowedValues": _SI_NO},
    # ── View (doc 61: Tipo de Vista; doc 69: + Filtro Despliegue 2021) ─────
    {"name": "Tipo de Vista", "level": "view", "view": "physical", "dataType": "list",
     "defaultValue": "Regular", "allowedValues": ["Regular", "Personalizada"],
     "description": "Regular = generada desde sources; Personalizada = User-Defined SQL (verbatim)."},
    {"name": "Filtro Despliegue 2021", "level": "view", "view": "physical", "dataType": "list",
     "defaultValue": "NO", "allowedValues": ["NO", "SI"]},
    # ── Model (canvas) ─────────────────────────────────────────────────────
    {"name": "Database", "level": "canvas", "view": "physical", "dataType": "string",
     "defaultValue": None, "allowedValues": []},
    # Doc 68: UDP «Model» de Erwin — texto con default del estándar (imagen
    # #3); el valor real del XML por archivo (model_udp) pisa el default.
    {"name": "Archivo Base", "level": "canvas", "view": "physical", "dataType": "string",
     "defaultValue": "DDV Modelo de Datos Fisico Planeamiento Banca Minorista",
     "allowedValues": []},
]

# Variantes CONOCIDAS de los XML (norm_enum de la variante → grafía canónica).
# norm_enum ya absorbe case/trim/espacios múltiples; acá van solo las que no:
# typos del origen ("Snaptshot"), tildes y guiones con espacios. Indexados por (level,
# nombre): aplican a AMBAS facetas (los typos de valor son los mismos).
ALIASES: dict[tuple[str, str], dict[str, str]] = {
    ("table", "TIPO DE CARGA"): {
        # Canónica = «Snapshot» (owner 2026-09-09): el typo del estándar y los
        # paréntesis pegados convergen hacia ella. «Tipo 1 (Historia …)» quedó
        # fuera del catálogo ⇒ sin destino (rige el default y va al reporte).
        norm_enum("Tipo 4 (Historia Snaptshot)"): "Tipo 4 (Historia Snapshot)",
        norm_enum("Tipo 4(Historia Snaptshot)"): "Tipo 4 (Historia Snapshot)",
        norm_enum("Tipo 4(Historia Snapshot)"): "Tipo 4 (Historia Snapshot)",
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


def fixed_lookup() -> dict[tuple[str, str, str], dict]:
    """{(level, view, norm_enum(name)) → def fija}. Para mapear defs del XML
    por nombre case-insensitive dentro de su faceta y para el seed/UI."""
    return {(d["level"], d["view"], norm_enum(d["name"])): d for d in FIXED_UDPS}


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
