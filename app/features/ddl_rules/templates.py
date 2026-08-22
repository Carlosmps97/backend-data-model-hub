"""Plantillas del picker "New rule" (pantalla 16d) + las 7 REGLAS SEMILLA del
spec §8 — TODAS con UDPs reales del catálogo (doc 30 A4). Las semillas también
son los fixtures de los golden tests y el contenido del CTA "Start with the 7
starter rules" del catálogo vacío (doc 30 D4): el front las manda por
`POST /api/standards/apply` — acá no se siembra nada solo.

El lookup `vacuum_map` referencia su UDP por NOMBRE; `templates_payload`
resuelve `fromUdpId` contra las definiciones vivas de la BD.
"""
from __future__ import annotations

SEED_RULES: list[dict] = [
    {   # 8.1 — hashear columnas críticas en la vista TÉCNICA (¡LIKE anclado!)
        "name": "enmascarar_dac", "kind": "rule", "target": "column",
        "description": "Hashea columnas de alta criticidad en la vista técnica",
        "condition": 'columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\'',
        "action": {"expression": "sha2({col}, 512)", "alias": "{columna.nombre}"},
        "appliesTo": ["ddl.vista_tecnica"], "priority": 100,
        "enabled": True,
    },
    {   # pedido owner 07-20 — vista de NEGOCIO (on canvas): la columna DAC se
        # envuelve con la función de desencriptación de Databricks; el 2º
        # parámetro es lo que va después de 'DAC-' en el valor del UDP
        # (DAC-TARJETA → 'TARJETA'), resuelto vía el lookup dac_map.
        "name": "desencriptar_dac_negocio", "kind": "rule", "target": "column",
        "description": "Desencripta columnas DAC en la vista de negocio (bcp_ddv_desencrypt)",
        "condition": 'columna.udp["Clasificacion del Dato"] LIKE \'DAC-%\'',
        "action": {"expression": "bcp_ddv_desencrypt({col}, '{lookup:dac_map}')",
                   "alias": "{columna.nombre}"},
        "appliesTo": ["ddl.vista_negocio"], "priority": 100,
        "enabled": True,
    },
    {   # 8.2 — tags de gobierno por columna (una sentencia por columna)
        "name": "tags_clasificacion", "kind": "rule", "target": "column",
        "description": "Tag de gobierno con la clasificación del dato de cada columna",
        "condition": 'columna.udp["Clasificacion del Dato"] <> \'No Definido\'',
        "action": {"tags": {"clasificacion_dato": "{udp:Clasificacion del Dato}"}},
        "appliesTo": ["ddl.tabla_fisica"], "priority": 50, "enabled": True,
    },
    {   # 8.3 — tags a nivel de tabla
        "name": "tags_tabla", "kind": "rule", "target": "table",
        "description": "Tags de dominio/estado/tipo de entidad a nivel de tabla",
        "condition": 'tabla.udp["Dominio Principal"] IS NOT NULL',
        "action": {"tags": {"dominio_principal": "{udp:Dominio Principal}",
                            "estado_cloud": "{udp:Estado Cloud}",
                            "tipo_entidad": "{udp:Tipo de Entidad}"}},
        "appliesTo": ["ddl.tabla_fisica"], "priority": 50, "enabled": True,
    },
    {   # 8.4 — retención de vacuum desde UDP (vía lookup). Condición ABIERTA
        # (aplica a toda tabla física): en el DDV real NINGUNA tabla asigna
        # "Frecuencia Vacuum" (usedBy=0, viven del default de Erwin CUSTOM_90
        # days). El "sin valor → default" se resuelve en el LOOKUP (campo
        # `default`), no en el motor (decisión owner 07-21, enfoque B —
        # 30b-HALLAZGOS-UDP-DEFAULTS.md). Una tabla con valor explícito mapea a
        # lo suyo; sin valor cae al default del lookup.
        "name": "tblproperties_vacuum", "kind": "rule", "target": "table",
        "description": "delta.deletedFileRetentionDuration según Frecuencia Vacuum (default del lookup si no hay valor)",
        "condition": "",
        "action": {"tblproperties": {"delta.deletedFileRetentionDuration": "{lookup:vacuum_map}"}},
        "appliesTo": ["ddl.tabla_fisica"], "priority": 40, "enabled": True,
    },
    {   # 8.5 — tabla de rechazos (todo STRING, sin constraints)
        "name": "tabla_rechazos", "kind": "generator",
        "description": "Tabla de rechazos derivada de la física: todo STRING",
        "sourceArtifact": "ddl.tabla_fisica", "condition": "tabla.tipo = 'physical'",
        "action": {"emit": {
            "artifact": "ddl.tabla_rej", "type": "table",
            "schema": "{tabla.esquema}", "name": "{tabla.nombre}_rej",
            "columns": {"inherit": "all", "force_type": "STRING",
                        "strip": ["not_null", "pk", "fk", "default", "check"]},
            "add_columns": [{"name": "rej_motivo", "type": "STRING"},
                            {"name": "rej_fecha", "type": "TIMESTAMP"},
                            {"name": "rej_archivo", "type": "STRING"}],
        }},
        "priority": 200, "enabled": True,
    },
    {   # 8.6 — vista sobre la tabla de rechazos (cascada)
        "name": "vista_rechazos", "kind": "generator",
        "description": "Vista de rechazos en el esquema espejo _v",
        "sourceArtifact": "ddl.tabla_rej", "condition": "true",
        "action": {"emit": {"artifact": "ddl.vista_rej", "type": "view",
                            "schema": "{tabla.esquema}_v", "name": "{tabla.nombre}_rej",
                            "columns": {"inherit": "all"}}},
        "priority": 190, "enabled": True,
    },
    {   # 8.7 — vista técnica (la decora enmascarar_dac vía appliesTo).
        # "Regular" es el DEFAULT: una tabla sin el UDP asignado también genera
        # su vista técnica; solo 'Personalizada' queda fuera (pedido owner —
        # en el DDV real ninguna tabla trae "Tipo de Vista" asignado todavía).
        "name": "vista_tecnica", "kind": "generator",
        "description": "Vista técnica en el esquema espejo _v (Regular por default)",
        "sourceArtifact": "ddl.tabla_fisica",
        "condition": ('tabla.udp["Tipo de Vista"] IS NULL '
                      'OR tabla.udp["Tipo de Vista"] = \'Regular\''),
        "action": {"emit": {"artifact": "ddl.vista_tecnica", "type": "view",
                            "schema": "{tabla.esquema}_v", "name": "{tabla.nombre}",
                            "columns": {"inherit": "all"}}},
        "priority": 180, "enabled": True,
    },
]

# Los VLOOKUP del ruleset (spec §6.7) — completos, sobre los valores reales.
SEED_LOOKUPS: dict = {
    "vacuum_map": {
        "fromUdpName": "Frecuencia Vacuum", "fromLevel": "table",
        "values": {
            "CUSTOM_90 days": "interval 90 days",
            "DAILY_15 days": "interval 15 days",
            "WEEKLY_30 days": "interval 30 days",
            "BIWEEKLY_45 days": "interval 45 days",
            "MONTHLY_90 days": "interval 90 days",
            "QUARTERLY_180 days": "interval 180 days",
            "SEMIYEARLY_365 days": "interval 365 days",
            "YEARLY_730 days": "interval 730 days",
            "EVENTUAL_90 days": "interval 90 days",
        },
        # default (unmapped) = el "case de default desde la regla" (enfoque B):
        # una tabla SIN "Frecuencia Vacuum" asignada cae acá. = el default de
        # Erwin (CUSTOM_90 days) ya mapeado. Editable en Lookups & Functions.
        "default": "interval 90 days",
    },
    # DAC-XXXX → 'XXXX' (el 2º parámetro de bcp_ddv_desencrypt). Los valores
    # no-críticos ('No DAC', 'No Definido') quedan sin mapear con default null
    # → no se emite nada (además la condición LIKE 'DAC-%' ya los filtra).
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
    {"id": "mask-by-udp", "title": "Mask by UDP",
     "summary": "Clasificacion del Dato LIKE 'DAC-%' → sha2(col, 512)",
     "rule": _SEED_BY_NAME["enmascarar_dac"]},
    {"id": "decrypt-dac", "title": "Decrypt DAC columns",
     "summary": "DAC-TARJETA → bcp_ddv_desencrypt(col, 'TARJETA')",
     "rule": _SEED_BY_NAME["desencriptar_dac_negocio"]},
    {"id": "rejected-table", "title": "Generate rejected table",
     "summary": "tbl_x → tbl_x_rej (all STRING)",
     "rule": _SEED_BY_NAME["tabla_rechazos"]},
    {"id": "technical-view", "title": "Generate technical view",
     "summary": "esquema → esquema_v",
     "rule": _SEED_BY_NAME["vista_tecnica"]},
    {"id": "governance-tags", "title": "Apply governance tags",
     "summary": "Clasificacion del Dato → SET TAGS",
     "rule": _SEED_BY_NAME["tags_clasificacion"]},
    {"id": "blank", "title": "Blank rule",
     "summary": "Start from scratch and configure every field yourself.",
     "rule": {"name": "", "kind": "rule", "target": "column", "condition": "",
              "action": {}, "appliesTo": [], "priority": 100, "enabled": True}},
]
