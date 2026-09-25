"""Plantillas del picker "New rule" (pantalla 16d) + el RULESET BASE que
reproduce los DDL de la macro BCP (doc 76 §5 · doc 93) + la homologación de
tipos CHAR(n) → VARCHAR(n) (doc 90): 12 reglas + 5 generadores —
TODAS con UDPs reales del catálogo fijo (doc 68/69). Doc 93: tag
`updateFrequency`; `isDAC` de las vistas de negocio según las columnas que
PROYECTAN (`ANY_COLUMN`); particiones desde el UDP «Particion». Las semillas también son
los fixtures de los golden tests y el contenido del CTA "Restore the base rule
set" del catálogo vacío (doc 30 D4): el front las manda por
`POST /api/projects/{pid}/standards/apply` — acá no se siembra nada solo. En
una BD nueva las siembra `scripts/seed_ddl_export_rules.py` (el one-shot).

Los lookups referencian su UDP por NOMBRE (`fromUdpName`); `templates_payload`
resuelve `fromUdpId` contra las definiciones vivas del proyecto.

Los 7 artefactos de la macro (doc 76 §1):
  1 ddl.tabla_fisica      tabla física (CREATE del front + reglas)
  2 ddl.tabla_rej         tabla de rechazos (todo STRING salvo particiones, + tiporeject)
  3 ddl.vista_tecnica     vista técnica NoDAC   {esq}_v.{tabla}        (sin columnas DAC si la tabla es DAC)
  4 ddl.vista_tecnica_dac vista técnica DAC     {esq}_v.{tabla}dac     (solo tablas DAC; columnas DAC desencriptadas)
  5 ddl.vista_rej         vista rechazos NoDAC  {esq}_v.{tabla}_rej
  6 ddl.vista_rej_dac     vista rechazos DAC    {esq}_v.{tabla}dac_rej (solo tablas DAC)
  7 ddl.vista_negocio     vistas de negocio (_vu) modeladas en Erwin/canvas
"""
from __future__ import annotations

import copy

from .output import OUTPUT_DEFAULTS

TABLE_ARTIFACTS = ["ddl.tabla_fisica", "ddl.tabla_rej"]
NODAC_VIEWS = ["ddl.vista_tecnica", "ddl.vista_rej"]
DAC_GENERATED_VIEWS = ["ddl.vista_tecnica_dac", "ddl.vista_rej_dac"]
DAC_VIEWS = DAC_GENERATED_VIEWS + ["ddl.vista_negocio"]
ALL_ARTIFACTS = TABLE_ARTIFACTS + NODAC_VIEWS + DAC_VIEWS

_COL_IS_DAC = 'columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\''
_TABLE_IS_DAC = 'tabla.udp["Clasificacion del Dato"] = \'DAC\''
# Doc 93 D9: la vista de negocio es DAC si PROYECTA alguna columna DAC-*.
_ANY_DAC_COLUMN = f"ANY_COLUMN({_COL_IS_DAC})"

SEED_RULES: list[dict] = [
    # ── Reglas de COLUMNA (decoran los SELECT / tags por columna) ──────────
    {   # 3/5 · vista NoDAC de una tabla DAC: las columnas DAC-* se QUITAN
        "name": "excluir_dac_vista_sin_dac", "kind": "rule", "target": "column",
        "description": "Quita las columnas DAC-* de las vistas NoDAC cuando la tabla es DAC",
        "condition": f"{_TABLE_IS_DAC} AND {_COL_IS_DAC}",
        "action": {"exclude": True},
        "appliesTo": list(NODAC_VIEWS), "priority": 100, "enabled": True,
    },
    {   # 4/6/7 · vistas DAC y de negocio: la columna DAC sale desencriptada con
        # el sufijo del valor UDP (DAC-DOCUMENTO → 'DOCUMENTO') vía lookup dac_map.
        "name": "desencriptar_dac", "kind": "rule", "target": "column",
        "description": "Desencripta las columnas DAC-* en las vistas DAC y de negocio (bcp_encrypt_function.decrypt_column_view)",
        "condition": _COL_IS_DAC,
        "action": {"expression": "bcp_encrypt_function.decrypt_column_view({col}, '{lookup:dac_map}')",
                   "alias": "{columna.nombre}"},
        "appliesTo": list(DAC_VIEWS), "priority": 100, "enabled": True,
    },
    {   # 1/2/4/6/7 · tag de columna: 'DAC' = sufijo (DOCUMENTO, NOMBRE, …)
        "name": "tags_dac_columna", "kind": "rule", "target": "column",
        "description": "ALTER … ALTER COLUMN … SET TAGS ('DAC' = '<sufijo>') en las columnas DAC-*",
        "condition": _COL_IS_DAC,
        "action": {"tags": {"DAC": "{lookup:dac_map}"}},
        "appliesTo": TABLE_ARTIFACTS + DAC_VIEWS, "priority": 50, "enabled": True,
    },
    {   # 1/2 · homologación de tipos (pedido owner 2026-09-11, doc 90): toda
        # columna CHAR(n) sale VARCHAR(n) en la física y la _rej (misma
        # longitud). Acción genérica `types` = {tipo base origen: destino};
        # más pares, o una condición por UDP/dominio, se editan en la regla.
        # Las vistas no declaran tipos: fuera de appliesTo.
        "name": "char_a_varchar", "kind": "rule", "target": "column",
        "description": "Exporta las columnas CHAR(n) como VARCHAR(n), misma longitud (tabla física y _rej)",
        "condition": "",
        "action": {"types": {"CHAR": "VARCHAR"}},
        "appliesTo": list(TABLE_ARTIFACTS), "priority": 80, "enabled": True,
    },
    # ── Reglas de TABLA (objeto: tablas y vistas) ──────────────────────────
    {   # 1/2 · preámbulo comentado (la macro lo escribe siempre)
        "name": "drop_comentado", "kind": "rule", "target": "table",
        "description": "-- DROP TABLE IF EXISTS … comentado al inicio del CREATE (física y _rej)",
        "condition": "",
        "action": {"statements": {"before": ["-- DROP TABLE IF EXISTS {artefacto.ref};"]}},
        "appliesTo": list(TABLE_ARTIFACTS), "priority": 90, "enabled": True,
    },
    {   # todos · updateFrequency = prefijo de Frecuencia Vacuum (CUSTOM_90 days → CUSTOM).
        # Condición ABIERTA: sin valor cae al default del lookup (enfoque B, doc 30 §11).
        "name": "tags_update_frequency", "kind": "rule", "target": "table",
        "description": "SET TAGS ('updateFrequency' = …) según Frecuencia Vacuum (lookup update_frequency_map)",
        "condition": "",
        "action": {"tags": {"updateFrequency": "{lookup:update_frequency_map}"}},
        "appliesTo": list(ALL_ARTIFACTS), "priority": 60, "enabled": True,
    },
    {   # 1/2/4/6 · física, _rej y vistas técnicas DAC · isDAC según el UDP de
        # TABLA (No Definido/sin valor → False)
        "name": "tags_isdac", "kind": "rule", "target": "table",
        "description": "SET TAGS ('isDAC' = 'True'|'False') según Clasificacion del Dato de la tabla (física, _rej y vistas técnicas DAC)",
        "condition": "",
        "action": {"tags": {"isDAC": "{lookup:dac_flag_map}"}},
        "appliesTo": TABLE_ARTIFACTS + DAC_GENERATED_VIEWS, "priority": 55, "enabled": True,
    },
    {   # 3/5 · las vistas NoDAC nunca exponen columnas DAC → False constante
        "name": "tags_isdac_sin_dac", "kind": "rule", "target": "table",
        "description": "SET TAGS ('isDAC' = 'False') en las vistas técnicas y de rechazos NoDAC",
        "condition": "",
        "action": {"tags": {"isDAC": "False"}},
        "appliesTo": list(NODAC_VIEWS), "priority": 55, "enabled": True,
    },
    {   # 7 · vista de negocio (_vu) que proyecta alguna columna DAC-* (doc 93 D9)
        "name": "tags_isdac_vista_negocio", "kind": "rule", "target": "table",
        "description": "SET TAGS ('isDAC' = 'True') en las vistas de negocio que proyectan alguna columna DAC-*",
        "condition": _ANY_DAC_COLUMN,
        "action": {"tags": {"isDAC": "True"}},
        "appliesTo": ["ddl.vista_negocio"], "priority": 55, "enabled": True,
    },
    {   # 7 · vista de negocio (_vu) sin columnas DAC-* (doc 93 D9)
        "name": "tags_isdac_vista_negocio_sin_dac", "kind": "rule", "target": "table",
        "description": "SET TAGS ('isDAC' = 'False') en las vistas de negocio que no proyectan columnas DAC-*",
        "condition": f"NOT {_ANY_DAC_COLUMN}",
        "action": {"tags": {"isDAC": "False"}},
        "appliesTo": ["ddl.vista_negocio"], "priority": 55, "enabled": True,
    },
    {   # 1/2 · retención delta según Frecuencia Vacuum (vía lookup). Condición
        # ABIERTA: en el DDV real NINGUNA tabla asigna "Frecuencia Vacuum"
        # (viven del default de Erwin CUSTOM_90 days) → el "sin valor → default"
        # se resuelve en el LOOKUP (campo `default`), no en el motor (decisión
        # owner 07-21, enfoque B — 30b-HALLAZGOS-UDP-DEFAULTS.md).
        "name": "tblproperties_vacuum", "kind": "rule", "target": "table",
        "description": "delta.logRetentionDuration y delta.deletedFileRetentionDuration según Frecuencia Vacuum (default del lookup si no hay valor)",
        "condition": "",
        "action": {"tblproperties": {"delta.logRetentionDuration": "{lookup:vacuum_map}",
                                     "delta.deletedFileRetentionDuration": "{lookup:vacuum_map}"}},
        "appliesTo": list(TABLE_ARTIFACTS), "priority": 40, "enabled": True,
    },
    {   # doc 73/76/93 · layout: las columnas de partición SON las que tienen
        # PART_nn en el UDP «Particion» (el flag de partición no se usa); se
        # emiten al FINAL del CREATE en orden PART_01, PART_02… y el
        # PARTITIONED BY lleva solo los nombres. El orden físico del modelo no
        # cambia. Sin condición.
        "name": "particiones_al_final", "kind": "rule", "target": "table",
        "description": "Particiones = columnas con PART_nn en el UDP Particion; al final del CREATE TABLE en orden PART_01, PART_02… (PARTITIONED BY solo con nombres)",
        "condition": "",
        "action": {"layout": {"partitionColumns": "last", "partitionUdp": "Particion"}},
        "appliesTo": ["ddl.tabla_fisica"], "priority": 30, "enabled": True,
    },
    # ── Generadores (cascada) ──────────────────────────────────────────────
    {   # 2 · tabla de rechazos: todo STRING salvo las particiones (conservan su
        # tipo), sin constraints, + tiporeject al final (antes de las particiones)
        "name": "tabla_rechazos", "kind": "generator",
        "description": "Tabla de rechazos _rej: todo STRING (las particiones conservan su tipo) + tiporeject",
        "sourceArtifact": "ddl.tabla_fisica", "condition": "",
        "action": {"emit": {
            "artifact": "ddl.tabla_rej", "type": "table",
            "schema": "{tabla.esquema}", "name": "{tabla.nombre}_rej",
            "columns": {"inherit": "all", "force_type": "STRING", "keep_partition_type": True,
                        "strip": ["not_null", "pk", "fk", "default", "check"]},
            "add_columns": [{"name": "tiporeject", "type": "STRING"}],
        }},
        "priority": 200, "enabled": True,
    },
    {   # 3 · vista técnica NoDAC (siempre; la decora excluir_dac_vista_sin_dac)
        "name": "vista_tecnica", "kind": "generator",
        "description": "Vista técnica NoDAC en el esquema espejo _v (col AS col en orden físico)",
        "sourceArtifact": "ddl.tabla_fisica", "condition": "",
        "action": {"emit": {"artifact": "ddl.vista_tecnica", "type": "view",
                            "schema": "{tabla.esquema}_v", "name": "{tabla.nombre}",
                            "columns": {"inherit": "all"}}},
        "priority": 180, "enabled": True,
    },
    {   # 4 · vista técnica DAC (solo tablas DAC; la decora desencriptar_dac)
        "name": "vista_tecnica_dac", "kind": "generator",
        "description": "Vista técnica DAC ({tabla}dac) solo para tablas DAC, con las columnas DAC desencriptadas",
        "sourceArtifact": "ddl.tabla_fisica", "condition": _TABLE_IS_DAC,
        "action": {"emit": {"artifact": "ddl.vista_tecnica_dac", "type": "view",
                            "schema": "{tabla.esquema}_v", "name": "{tabla.nombre}dac",
                            "columns": {"inherit": "all"}}},
        "priority": 170, "enabled": True,
    },
    {   # 5 · vista de rechazos NoDAC (cascada sobre la _rej; tiporeject al final)
        "name": "vista_rechazos", "kind": "generator",
        "description": "Vista de rechazos NoDAC en el esquema espejo _v",
        "sourceArtifact": "ddl.tabla_rej", "condition": "",
        "action": {"emit": {"artifact": "ddl.vista_rej", "type": "view",
                            "schema": "{tabla.esquema}_v", "name": "{tabla.nombre}_rej",
                            "columns": {"inherit": "all"}}},
        "priority": 160, "enabled": True,
    },
    {   # 6 · vista de rechazos DAC (solo tablas DAC)
        "name": "vista_rechazos_dac", "kind": "generator",
        "description": "Vista de rechazos DAC ({tabla}dac_rej) solo para tablas DAC",
        "sourceArtifact": "ddl.tabla_rej", "condition": _TABLE_IS_DAC,
        "action": {"emit": {"artifact": "ddl.vista_rej_dac", "type": "view",
                            "schema": "{tabla.esquema}_v", "name": "{tabla.nombre}dac_rej",
                            "columns": {"inherit": "all"}}},
        "priority": 150, "enabled": True,
    },
]

# Los VLOOKUP del ruleset (spec §6.7) — el "case when" de la macro, completos
# sobre los valores reales del catálogo fijo (standard_udps.py).
_VACUUM_VALUES = ["CUSTOM_90 days", "DAILY_15 days", "WEEKLY_30 days", "BIWEEKLY_45 days",
                  "MONTHLY_90 days", "QUARTERLY_180 days", "SEMIYEARLY_365 days",
                  "YEARLY_730 days", "EVENTUAL_90 days"]

SEED_LOOKUPS: dict = {
    # Frecuencia Vacuum → días de retención delta ('CUSTOM_90 days' → '90 days').
    # default (unmapped) = el "case de default desde la regla" (enfoque B): una
    # tabla SIN "Frecuencia Vacuum" asignada cae acá (= CUSTOM_90 days de Erwin).
    "vacuum_map": {
        "fromUdpName": "Frecuencia Vacuum", "fromLevel": "table",
        "values": {v: f"{v.split('_', 1)[1]}" for v in _VACUUM_VALUES},
        "default": "90 days",
    },
    # Frecuencia Vacuum → prefijo para el tag updateFrequency ('DAILY_15 days' → 'DAILY').
    "update_frequency_map": {
        "fromUdpName": "Frecuencia Vacuum", "fromLevel": "table",
        "values": {v: v.split("_", 1)[0] for v in _VACUUM_VALUES},
        "default": "CUSTOM",
    },
    # Clasificacion del Dato (TABLA) → tag isDAC. No Definido / sin valor → False.
    "dac_flag_map": {
        "fromUdpName": "Clasificacion del Dato", "fromLevel": "table",
        "values": {"DAC": "True", "No DAC": "False", "No Definido": "False"},
        "default": "False",
    },
    # DAC-XXXX → 'XXXX' (2º parámetro de decrypt_column_view y valor del tag
    # 'DAC'). Los no-críticos ('No DAC', 'No Definido') quedan sin mapear con
    # default null → no se emite nada (además LIKE 'DAC-%' ya los filtra).
    "dac_map": {
        "fromUdpName": "Clasificacion del Dato", "fromLevel": "column",
        "values": {
            "DAC-DOCUMENTO": "DOCUMENTO",
            "DAC-NOMBRE": "NOMBRE",
            "DAC-DIRECCION": "DIRECCION",
            "DAC-TELEFONO": "TELEFONO",
            "DAC-CUENTA": "CUENTA",
            "DAC-TARJETA": "TARJETA",
            "DAC-EMAIL": "EMAIL",
            "DAC-BIOMETRICO": "BIOMETRICO",
            "DAC-IMAGENVOZ": "IMAGENVOZ",
            "DAC-FIRMA": "FIRMA",
            "DAC-GLOSADAC": "GLOSADAC",
        },
        "default": None,
    },
}

_SEED_BY_NAME = {r["name"]: r for r in SEED_RULES}

# Plantillas del modal 16d (los `rule` son pre-cargas del editor, editables).
TEMPLATES: list[dict] = [
    {"id": "decrypt-dac", "title": "Decrypt DAC columns",
     "summary": "DAC-DOCUMENTO → bcp_encrypt_function.decrypt_column_view(col, 'DOCUMENTO')",
     "rule": _SEED_BY_NAME["desencriptar_dac"]},
    {"id": "exclude-dac", "title": "Exclude DAC columns",
     "summary": "Drop the DAC-* columns from the NoDAC views of a DAC table",
     "rule": _SEED_BY_NAME["excluir_dac_vista_sin_dac"]},
    {"id": "rejected-table", "title": "Generate rejected table",
     "summary": "tbl_x → tbl_x_rej (all STRING, partitions keep their type, + tiporeject)",
     "rule": _SEED_BY_NAME["tabla_rechazos"]},
    {"id": "technical-view", "title": "Generate technical view",
     "summary": "esquema → esquema_v · col AS col in physical order",
     "rule": _SEED_BY_NAME["vista_tecnica"]},
    {"id": "governance-tags", "title": "Apply governance tags",
     "summary": "Frecuencia Vacuum → SET TAGS ('updateFrequency' = 'MONTHLY')",
     "rule": _SEED_BY_NAME["tags_update_frequency"]},
    {"id": "free-statements", "title": "Free statements",
     "summary": "-- DROP TABLE IF EXISTS … before the CREATE (any SQL text with placeholders)",
     "rule": _SEED_BY_NAME["drop_comentado"]},
    {"id": "partitions-last", "title": "Partition columns last",
     "summary": "Partition columns from the UDP 'Particion' (PART_nn), at the end of the CREATE",
     "rule": _SEED_BY_NAME["particiones_al_final"]},
    {"id": "map-types", "title": "Map data types",
     "summary": "CHAR(10) → VARCHAR(10): rename a base type on export, keeping each column's length",
     "rule": _SEED_BY_NAME["char_a_varchar"]},
    {"id": "tag-by-columns", "title": "Tag by the columns an object exposes",
     "summary": "ANY_COLUMN(columna.udp[\"Clasificacion del Dato\"] LIKE 'DAC-%') → SET TAGS ('isDAC' = 'True')",
     "rule": _SEED_BY_NAME["tags_isdac_vista_negocio"]},
    {"id": "blank", "title": "Blank rule",
     "summary": "Start from scratch and configure every field yourself.",
     "rule": {"name": "", "kind": "rule", "target": "column", "condition": "",
              "action": {}, "appliesTo": [], "priority": 100, "enabled": True}},
]

# Doc 93 D1: Output settings de la semilla = convenciones de la macro BCP.
SEED_OUTPUT: dict = copy.deepcopy(OUTPUT_DEFAULTS)
