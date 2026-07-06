"""Seed greenfield del modelo nuevo "Data Model Hub" (R1d).

Borra SOLO las colecciones del modeler y las re-siembra con data demo coherente
con el diseño DMH-Erwin (Projects→Folders→Canvas, tablas/columnas/relaciones
físicas tipo Databricks, parent domains, diccionario UDP por scope, naming_config
y versiones/requests para alimentar Home + Review).

⚠️  PRESERVA `column_catalog` (colección del agente `app-agents-modeler`): NUNCA
    se borra ni se toca. El wipe se limita a `MODELER_COLLECTIONS`.

Diseño del script (testeable sin DB):
- La construcción de la data vive en **funciones puras** (`build_*`) que devuelven
  listas de dicts ya con `_id`/`flgactive` listos para `insert_many`.
- `build_all()` ensambla el dataset completo y resuelve los ids referenciales.
- `async main()` conecta a Cosmos (Motor), borra las colecciones del modeler e
  inserta cada lista; reporta counts y confirma que `column_catalog` quedó intacta.

Run:  backend-data-model-hub/.venv/bin/python scripts/seed_modeler.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.naming import physicalize  # noqa: E402

# ── Colecciones del modeler (las ÚNICAS que el seed borra) ───────────────────
# `column_catalog` (del agente) NO está aquí a propósito: jamás se toca.
MODELER_COLLECTIONS: list[str] = [
    "projects",
    "folders",
    "subject_areas",      # = canvases (rename pendiente, fase posterior)
    "canonical_tables",
    "canonical_columns",
    "relationships",
    "views",
    "parent_domains",
    "glossary_terms",
    "naming_config",
    "changesets",
    "changeset_changes",  # un doc POR CAMBIO (el dict embebido topaba 2MB/doc)
    "users",              # auth propia (login usuario/contraseña)
    "roles",              # RBAC data-driven (matriz de permisos)
    "standards_versions", # versionado independiente de Data Standards (baseline)
]
PROTECTED_COLLECTION = "column_catalog"  # agente — preservar siempre

_NOW = datetime.now(timezone.utc).isoformat()


def _now() -> str:
    return _NOW


# ── Naming config (separador/case por scope) ─────────────────────────────────
# column: 'monto deuda dólares' → MTO_DEU_USD ; table: 'cuenta riesgo' → CTARIESGO
_COL_NAMING = {"separator": "_", "case": "upper"}
_TBL_NAMING = {"separator": "", "case": "upper"}


def build_naming_config() -> list[dict]:
    """`naming_config`: 1 doc por scope (`_id == scope`, como lee el repo)."""
    return [
        {"_id": "column", "scope": "column", **_COL_NAMING,
         "flgactive": True, "createdAt": _now(), "updatedAt": _now()},
        {"_id": "table", "scope": "table", **_TBL_NAMING,
         "flgactive": True, "createdAt": _now(), "updatedAt": _now()},
    ]


# ── Diccionario de abreviaturas (UDP) por scope ──────────────────────────────
# scope 'column': términos prime/class/modifier para nombres de columna.
# scope 'table' : set propio para nombres de tabla (físicos sin separador).
# (term, abbrev, wordType)
_DICT_COLUMN: list[tuple[str, str, str | None]] = [
    # prime (entidad/sujeto)
    ("cuenta", "CTA", "prime"),
    ("cliente", "CLI", "prime"),
    ("producto", "PRD", "prime"),
    ("sucursal", "SUC", "prime"),
    ("moneda", "MON", "prime"),
    ("empleado", "EMP", "prime"),
    ("movimiento", "MOV", "prime"),
    ("tarjeta", "TAR", "prime"),
    ("prestamo", "PRE", "prime"),
    ("riesgo", "RIESGO", "prime"),
    # class (clase/medida)
    ("monto", "MTO", "class"),
    ("deuda", "DEU", "class"),
    ("saldo", "SLD", "class"),
    ("importe", "IMP", "class"),
    ("identificador", "ID", "class"),
    ("codigo", "COD", "class"),
    ("nombre", "NOM", "class"),
    ("fecha", "FEC", "class"),
    ("descripcion", "DSC", "class"),
    ("tipo", "TIP", "class"),
    ("estado", "EST", "class"),
    ("limite", "LIM", "class"),
    ("tasa", "TAS", "class"),
    ("score", "SCO", "class"),
    # modifier (calificador)
    ("dolares", "USD", "modifier"),
    ("usd", "USD", "modifier"),
    ("soles", "PEN", "modifier"),
    ("apertura", "APE", "modifier"),
    ("vencimiento", "VTO", "modifier"),
    ("origen", "ORI", "modifier"),
    ("destino", "DES", "modifier"),
    ("alta", "ALT", "modifier"),
]
# scope 'table': abreviaturas para componer nombres de tabla (otro set).
_DICT_TABLE: list[tuple[str, str, str | None]] = [
    ("cuenta", "CTA", "prime"),
    ("cliente", "CLI", "prime"),
    ("producto", "PROD", "prime"),
    ("sucursal", "SUC", "prime"),
    ("moneda", "MON", "prime"),
    ("empleado", "EMP", "prime"),
    ("movimiento", "MOV", "prime"),
    ("tarjeta", "TARJ", "prime"),
    ("prestamo", "PREST", "prime"),
    ("riesgo", "RIESGO", "prime"),
    ("maestro", "MA", "modifier"),
    ("detalle", "DET", "modifier"),
    ("dimension", "DIM", "modifier"),
    ("hecho", "FACT", "modifier"),
]


def build_glossary_terms() -> list[dict]:
    """`glossary_terms`: scopes 'column' y 'table', con `wordType`."""
    out: list[dict] = []
    for scope, entries in (("column", _DICT_COLUMN), ("table", _DICT_TABLE)):
        for term, abbrev, word_type in entries:
            out.append({
                "_id": f"ad-{scope}-{abbrev.lower()}-{term.replace(' ', '')}",
                "term": term,
                "abbrev": abbrev,
                "scope": scope,
                "wordType": word_type,
                "flgactive": True,
                "createdAt": _now(),
                "updatedAt": _now(),
            })
    return out


def _column_mappings() -> dict[str, str]:
    """Mapa term→abbrev del scope 'column' para `physicalize` de columnas."""
    return {term: abbrev for term, abbrev, _ in _DICT_COLUMN}


# ── Parent domains (tipo por defecto; retroactivos, fuera de publish) ────────
# (id, name, defaultDataType, namingTerm, description)
_DOMAINS: list[tuple[str, str, str, str, str]] = [
    ("pd-importe", "Importe", "DECIMAL(18,2)", "monto",
     "Valor monetario con 2 decimales (saldos, montos, límites)."),
    ("pd-identificador", "Identificador", "BIGINT", "identificador",
     "Clave numérica entera para PKs/FKs surrogate."),
    ("pd-fecha", "Fecha", "DATE", "fecha",
     "Fecha calendario sin componente horario."),
    ("pd-codigo", "Código", "STRING", "codigo",
     "Código/texto corto (códigos, nombres, descripciones)."),
]


def build_parent_domains() -> list[dict]:
    """`parent_domains`: 4 dominios con `namingTerm`/`description`."""
    return [
        {"_id": did, "name": name, "defaultDataType": dtype,
         "namingTerm": term, "description": desc,
         "flgactive": True, "createdAt": _now(), "updatedAt": _now()}
        for did, name, dtype, term, desc in _DOMAINS
    ]


_DOMAIN_TYPE = {did: dtype for did, _, dtype, _, _ in _DOMAINS}


# ── Projects ─────────────────────────────────────────────────────────────────
_PROJECTS: list[tuple[str, str, str]] = [
    ("proj-core-banking", "Core Banking",
     "Modelo transaccional núcleo: cuentas, clientes, movimientos, sucursales."),
    ("proj-customer-360", "Customer 360",
     "Vista unificada del cliente: productos, tarjetas y contacto."),
    ("proj-risk-analytics", "Risk Analytics",
     "Analítica de riesgo crediticio: exposición, scoring y préstamos."),
    # — proyectos extra (más data dummy para el Home) —
    ("proj-marketing", "Marketing Analytics",
     "Campañas, leads, segmentación e interacciones de clientes."),
    ("proj-hr", "Human Resources",
     "Empleados, departamentos, puestos, nómina y asistencia."),
    ("proj-payments", "Payments",
     "Pagos, transferencias, beneficiarios y canales de pago."),
    ("proj-digital", "Digital Banking",
     "Banca digital: usuarios, sesiones, dispositivos y notificaciones."),
    ("proj-treasury", "Treasury",
     "Tesorería: posiciones, instrumentos, contrapartes y límites."),
    ("proj-fraud", "Fraud Detection",
     "Señales de fraude sobre pagos y transferencias."),
    ("proj-wealth", "Wealth Management",
     "Gestión patrimonial e inversiones (proyecto nuevo, sin canvas)."),
    ("proj-governance", "Data Governance",
     "Catálogo, linaje y calidad de datos (proyecto nuevo, sin canvas)."),
]


def build_projects() -> list[dict]:
    """`projects`: 3 base + 8 extra (para ver el Home con muchos proyectos)."""
    return [
        {"_id": pid, "name": name, "description": desc,
         "flgactive": True, "createdAt": _now(), "updatedAt": _now()}
        for pid, name, desc in _PROJECTS
    ]


# ── Folders (jerarquía anidada en Core Banking) ──────────────────────────────
# (id, projectId, parentFolderId, name, order)
_FOLDERS: list[tuple[str, str, str | None, str, int]] = [
    ("fld-deposits", "proj-core-banking", None, "Deposits", 0),
    ("fld-lending", "proj-core-banking", None, "Lending", 1),
    ("fld-lending-collateral", "proj-core-banking", "fld-lending", "Collateral", 0),
]


def build_folders() -> list[dict]:
    """`folders`: Deposits y Lending en Core Banking, con subcarpeta Collateral."""
    return [
        {"_id": fid, "projectId": pid, "parentFolderId": parent,
         "name": name, "order": order,
         "flgactive": True, "createdAt": _now(), "updatedAt": _now()}
        for fid, pid, parent, name, order in _FOLDERS
    ]


# ── Tablas + columnas canónicas (físicas, tipo Databricks) ───────────────────
# Cada tabla: (id, logicalName, schema, description, columns)
#   columna: (colKey, logicalName, parentDomainId, pk, fk, nullable, partition, desc)
# colKey es único dentro de la tabla; el _id de columna = f"{tableId}.{colKey}".
def _tables_spec() -> list[dict]:
    return [
        {
            "id": "ct-cliente", "logical": "cliente", "schema": "core",
            "desc": "Maestro de clientes (personas y empresas) de la entidad.",
            "cols": [
                ("id_cliente", "identificador cliente", "pd-identificador", True, False, False, False,
                 "Identificador único del cliente (PK surrogate)."),
                ("nombre", "nombre cliente", "pd-codigo", False, False, False, False,
                 "Razón social o nombre completo del cliente."),
                ("tipo", "tipo cliente", "pd-codigo", False, False, False, False,
                 "Segmento del cliente (PERSONA/EMPRESA)."),
                ("fecha_alta", "fecha alta", "pd-fecha", False, False, True, False,
                 "Fecha de alta del cliente en la entidad."),
            ],
        },
        {
            "id": "ct-sucursal", "logical": "sucursal", "schema": "core",
            "desc": "Catálogo de sucursales/oficinas comerciales.",
            "cols": [
                ("id_sucursal", "identificador sucursal", "pd-identificador", True, False, False, False,
                 "Identificador único de la sucursal (PK)."),
                ("nombre", "nombre sucursal", "pd-codigo", False, False, False, False,
                 "Nombre de la sucursal."),
                ("codigo", "codigo sucursal", "pd-codigo", False, False, False, False,
                 "Código corto de la sucursal."),
            ],
        },
        {
            "id": "ct-moneda", "logical": "moneda", "schema": "ref",
            "desc": "Catálogo de monedas (ISO 4217).",
            "cols": [
                ("id_moneda", "identificador moneda", "pd-identificador", True, False, False, False,
                 "Identificador de la moneda (PK)."),
                ("codigo", "codigo moneda", "pd-codigo", False, False, False, False,
                 "Código ISO de la moneda (USD/PEN)."),
                ("nombre", "nombre moneda", "pd-codigo", False, False, False, False,
                 "Nombre de la moneda."),
            ],
        },
        {
            "id": "ct-producto", "logical": "producto", "schema": "core",
            "desc": "Catálogo de productos financieros ofrecidos.",
            "cols": [
                ("id_producto", "identificador producto", "pd-identificador", True, False, False, False,
                 "Identificador del producto (PK)."),
                ("nombre", "nombre producto", "pd-codigo", False, False, False, False,
                 "Nombre comercial del producto."),
                ("tipo", "tipo producto", "pd-codigo", False, False, False, False,
                 "Familia del producto (CUENTA/TARJETA/PRESTAMO)."),
            ],
        },
        {
            "id": "ct-empleado", "logical": "empleado", "schema": "core",
            "desc": "Maestro de empleados (gestores, analistas).",
            "cols": [
                ("id_empleado", "identificador empleado", "pd-identificador", True, False, False, False,
                 "Identificador del empleado (PK)."),
                ("nombre", "nombre empleado", "pd-codigo", False, False, False, False,
                 "Nombre del empleado."),
                ("id_sucursal", "identificador sucursal", "pd-identificador", False, True, True, False,
                 "Sucursal a la que pertenece el empleado (FK→sucursal)."),
            ],
        },
        {
            "id": "ct-cuenta", "logical": "cuenta", "schema": "core",
            "desc": "Cuentas de depósito/ahorro del cliente.",
            "cols": [
                ("id_cuenta", "identificador cuenta", "pd-identificador", True, False, False, False,
                 "Identificador único de la cuenta (PK)."),
                ("id_cliente", "identificador cliente", "pd-identificador", False, True, False, False,
                 "Cliente titular de la cuenta (FK→cliente)."),
                ("id_sucursal", "identificador sucursal", "pd-identificador", False, True, True, False,
                 "Sucursal de apertura (FK→sucursal)."),
                ("id_moneda", "identificador moneda", "pd-identificador", False, True, False, False,
                 "Moneda de la cuenta (FK→moneda)."),
                ("saldo", "saldo cuenta", "pd-importe", False, False, False, False,
                 "Saldo disponible de la cuenta."),
                ("fecha_apertura", "fecha apertura", "pd-fecha", False, False, True, False,
                 "Fecha de apertura de la cuenta."),
            ],
        },
        {
            "id": "ct-movimiento", "logical": "movimiento", "schema": "core",
            "desc": "Movimientos/transacciones sobre las cuentas (alta cardinalidad).",
            "cols": [
                ("id_movimiento", "identificador movimiento", "pd-identificador", True, False, False, False,
                 "Identificador del movimiento (PK)."),
                ("id_cuenta", "identificador cuenta", "pd-identificador", False, True, False, False,
                 "Cuenta afectada por el movimiento (FK→cuenta)."),
                ("monto", "monto movimiento", "pd-importe", False, False, False, False,
                 "Importe del movimiento."),
                ("tipo", "tipo movimiento", "pd-codigo", False, False, False, False,
                 "Tipo de movimiento (DEBITO/CREDITO)."),
                ("fecha", "fecha movimiento", "pd-fecha", False, False, False, True,
                 "Fecha contable del movimiento (columna de partición)."),
            ],
        },
        {
            "id": "ct-tarjeta", "logical": "tarjeta", "schema": "cards",
            "desc": "Tarjetas (débito/crédito) emitidas a clientes.",
            "cols": [
                ("id_tarjeta", "identificador tarjeta", "pd-identificador", True, False, False, False,
                 "Identificador de la tarjeta (PK)."),
                ("id_cliente", "identificador cliente", "pd-identificador", False, True, False, False,
                 "Cliente titular de la tarjeta (FK→cliente)."),
                ("id_cuenta", "identificador cuenta", "pd-identificador", False, True, True, False,
                 "Cuenta asociada a la tarjeta (FK→cuenta)."),
                ("tipo", "tipo tarjeta", "pd-codigo", False, False, False, False,
                 "Tipo de tarjeta (DEBITO/CREDITO)."),
                ("limite", "limite tarjeta", "pd-importe", False, False, True, False,
                 "Límite de crédito de la tarjeta."),
                ("estado", "estado tarjeta", "pd-codigo", False, False, False, False,
                 "Estado de la tarjeta (ACTIVA/BLOQUEADA)."),
            ],
        },
        {
            "id": "ct-prestamo", "logical": "prestamo", "schema": "lending",
            "desc": "Préstamos/créditos otorgados a clientes.",
            "cols": [
                ("id_prestamo", "identificador prestamo", "pd-identificador", True, False, False, False,
                 "Identificador del préstamo (PK)."),
                ("id_cliente", "identificador cliente", "pd-identificador", False, True, False, False,
                 "Cliente prestatario (FK→cliente)."),
                ("id_producto", "identificador producto", "pd-identificador", False, True, True, False,
                 "Producto crediticio (FK→producto)."),
                ("monto", "monto prestamo", "pd-importe", False, False, False, False,
                 "Monto principal del préstamo."),
                ("tasa", "tasa prestamo", "pd-importe", False, False, True, False,
                 "Tasa de interés anual aplicada."),
                ("fecha_vencimiento", "fecha vencimiento", "pd-fecha", False, False, True, False,
                 "Fecha de vencimiento del préstamo."),
            ],
        },
        {
            "id": "ct-riesgo", "logical": "riesgo cliente", "schema": "risk",
            "desc": "Perfil de riesgo crediticio agregado por cliente.",
            "cols": [
                ("id_riesgo", "identificador riesgo", "pd-identificador", True, False, False, False,
                 "Identificador del registro de riesgo (PK)."),
                ("id_cliente", "identificador cliente", "pd-identificador", False, True, False, False,
                 "Cliente evaluado (FK→cliente)."),
                ("score", "score riesgo", "pd-importe", False, False, False, False,
                 "Score de riesgo del cliente (0-1000)."),
                ("deuda", "monto deuda dolares", "pd-importe", False, False, True, False,
                 "Exposición/deuda total del cliente en USD."),
                ("estado", "estado riesgo", "pd-codigo", False, False, False, False,
                 "Clasificación de riesgo (NORMAL/CPP/DUDOSO)."),
            ],
        },
        {
            "id": "ct-exposicion", "logical": "exposicion riesgo", "schema": "risk",
            "desc": "Exposición de riesgo por cliente y producto (snapshot mensual).",
            "cols": [
                ("id_exposicion", "identificador exposicion", "pd-identificador", True, False, False, False,
                 "Identificador de la exposición (PK)."),
                ("id_cliente", "identificador cliente", "pd-identificador", False, True, False, False,
                 "Cliente de la exposición (FK→cliente)."),
                ("id_producto", "identificador producto", "pd-identificador", False, True, True, False,
                 "Producto de la exposición (FK→producto)."),
                ("importe", "importe exposicion", "pd-importe", False, False, False, False,
                 "Importe expuesto."),
                ("fecha", "fecha exposicion", "pd-fecha", False, False, False, True,
                 "Mes de la exposición (columna de partición)."),
            ],
        },
    ] + _extra_tables_spec()


def _extra_tables_spec() -> list[dict]:
    """20 tablas extra (marketing/hr/payments/digital/treasury) para poblar
    Reporting y los proyectos nuevos con más data dummy."""
    ID, COD, IMP, FEC = "pd-identificador", "pd-codigo", "pd-importe", "pd-fecha"
    PK = (True, False, False, False)    # pk, fk, nullable, partition
    FK = (False, True, False, False)
    FKN = (False, True, True, False)    # FK nullable
    A = (False, False, True, False)     # atributo nullable
    R = (False, False, False, False)    # atributo requerido
    P = (False, False, False, True)     # columna de partición

    def tb(tid: str, logical: str, schema: str, desc: str, cols: list) -> dict:
        return {"id": tid, "logical": logical, "schema": schema, "desc": desc, "cols": cols}

    return [
        # ── Marketing ──
        tb("ct-campania", "campania", "marketing", "Campañas de marketing y promociones.", [
            ("id_campania", "identificador campania", ID, *PK, "Id de campaña (PK)."),
            ("nombre", "nombre campania", COD, *R, "Nombre de la campaña."),
            ("fecha_inicio", "fecha inicio", FEC, *A, "Fecha de inicio."),
            ("presupuesto", "monto presupuesto", IMP, *A, "Presupuesto asignado."),
        ]),
        tb("ct-lead", "lead comercial", "marketing", "Prospectos/leads generados por campañas.", [
            ("id_lead", "identificador lead", ID, *PK, "Id de lead (PK)."),
            ("id_cliente", "identificador cliente", ID, *FKN, "Cliente convertido (FK→cliente)."),
            ("id_campania", "identificador campania", ID, *FK, "Campaña origen (FK→campania)."),
            ("estado", "estado lead", COD, *R, "Estado del lead (NUEVO/GANADO/PERDIDO)."),
        ]),
        tb("ct-segmento", "segmento cliente", "marketing", "Segmentos comerciales de clientes.", [
            ("id_segmento", "identificador segmento", ID, *PK, "Id de segmento (PK)."),
            ("nombre", "nombre segmento", COD, *R, "Nombre del segmento."),
            ("descripcion", "descripcion segmento", COD, *A, "Descripción del segmento."),
        ]),
        tb("ct-interaccion", "interaccion cliente", "marketing", "Interacciones de clientes con campañas.", [
            ("id_interaccion", "identificador interaccion", ID, *PK, "Id de interacción (PK)."),
            ("id_campania", "identificador campania", ID, *FK, "Campaña (FK→campania)."),
            ("fecha", "fecha interaccion", FEC, *P, "Fecha de la interacción (partición)."),
            ("tipo", "tipo interaccion", COD, *R, "Tipo (EMAIL/CLICK/CALL)."),
        ]),
        # ── Human Resources ──
        tb("ct-departamento", "departamento", "hr", "Departamentos/áreas de la organización.", [
            ("id_departamento", "identificador departamento", ID, *PK, "Id de departamento (PK)."),
            ("nombre", "nombre departamento", COD, *R, "Nombre del departamento."),
        ]),
        tb("ct-puesto", "puesto laboral", "hr", "Catálogo de puestos.", [
            ("id_puesto", "identificador puesto", ID, *PK, "Id de puesto (PK)."),
            ("id_departamento", "identificador departamento", ID, *FK, "Departamento (FK)."),
            ("nombre", "nombre puesto", COD, *R, "Nombre del puesto."),
        ]),
        tb("ct-nomina", "nomina empleado", "hr", "Liquidaciones de nómina por empleado.", [
            ("id_nomina", "identificador nomina", ID, *PK, "Id de nómina (PK)."),
            ("id_empleado", "identificador empleado", ID, *FK, "Empleado (FK→empleado)."),
            ("monto", "monto nomina", IMP, *R, "Importe liquidado."),
            ("fecha", "fecha nomina", FEC, *P, "Período de liquidación (partición)."),
        ]),
        tb("ct-asistencia", "asistencia empleado", "hr", "Registro de asistencia de empleados.", [
            ("id_asistencia", "identificador asistencia", ID, *PK, "Id de asistencia (PK)."),
            ("id_empleado", "identificador empleado", ID, *FK, "Empleado (FK→empleado)."),
            ("fecha", "fecha asistencia", FEC, *P, "Fecha (partición)."),
        ]),
        # ── Payments ──
        tb("ct-canal", "canal pago", "payments", "Canales de pago disponibles.", [
            ("id_canal", "identificador canal", ID, *PK, "Id de canal (PK)."),
            ("nombre", "nombre canal", COD, *R, "Nombre del canal."),
        ]),
        tb("ct-beneficiario", "beneficiario", "payments", "Beneficiarios de transferencias.", [
            ("id_beneficiario", "identificador beneficiario", ID, *PK, "Id de beneficiario (PK)."),
            ("id_cliente", "identificador cliente", ID, *FKN, "Cliente dueño (FK→cliente)."),
            ("nombre", "nombre beneficiario", COD, *R, "Nombre del beneficiario."),
        ]),
        tb("ct-pago", "pago", "payments", "Pagos realizados por clientes.", [
            ("id_pago", "identificador pago", ID, *PK, "Id de pago (PK)."),
            ("id_cliente", "identificador cliente", ID, *FK, "Cliente pagador (FK→cliente)."),
            ("id_canal", "identificador canal", ID, *FK, "Canal de pago (FK→canal)."),
            ("monto", "monto pago", IMP, *R, "Importe pagado."),
            ("fecha", "fecha pago", FEC, *P, "Fecha del pago (partición)."),
        ]),
        tb("ct-transferencia", "transferencia", "payments", "Transferencias entre cuentas/beneficiarios.", [
            ("id_transferencia", "identificador transferencia", ID, *PK, "Id de transferencia (PK)."),
            ("id_pago", "identificador pago", ID, *FK, "Pago asociado (FK→pago)."),
            ("id_beneficiario", "identificador beneficiario", ID, *FKN, "Beneficiario (FK→beneficiario)."),
            ("monto", "monto transferencia", IMP, *R, "Importe transferido."),
        ]),
        # ── Digital Banking ──
        tb("ct-usuario-digital", "usuario digital", "digital", "Usuarios de banca digital.", [
            ("id_usuario", "identificador usuario", ID, *PK, "Id de usuario (PK)."),
            ("id_cliente", "identificador cliente", ID, *FK, "Cliente asociado (FK→cliente)."),
            ("estado", "estado usuario", COD, *R, "Estado (ACTIVO/BLOQUEADO)."),
        ]),
        tb("ct-sesion", "sesion digital", "digital", "Sesiones de los usuarios digitales.", [
            ("id_sesion", "identificador sesion", ID, *PK, "Id de sesión (PK)."),
            ("id_usuario", "identificador usuario", ID, *FK, "Usuario (FK→usuario digital)."),
            ("fecha", "fecha sesion", FEC, *P, "Fecha de la sesión (partición)."),
        ]),
        tb("ct-dispositivo", "dispositivo", "digital", "Dispositivos registrados por usuario.", [
            ("id_dispositivo", "identificador dispositivo", ID, *PK, "Id de dispositivo (PK)."),
            ("id_usuario", "identificador usuario", ID, *FKN, "Usuario (FK→usuario digital)."),
            ("tipo", "tipo dispositivo", COD, *R, "Tipo (MOBILE/WEB)."),
        ]),
        tb("ct-notificacion", "notificacion", "digital", "Notificaciones enviadas a usuarios.", [
            ("id_notificacion", "identificador notificacion", ID, *PK, "Id de notificación (PK)."),
            ("id_usuario", "identificador usuario", ID, *FK, "Usuario destino (FK→usuario digital)."),
            ("fecha", "fecha notificacion", FEC, *P, "Fecha de envío (partición)."),
        ]),
        # ── Treasury ──
        tb("ct-contraparte", "contraparte", "treasury", "Contrapartes de operaciones de tesorería.", [
            ("id_contraparte", "identificador contraparte", ID, *PK, "Id de contraparte (PK)."),
            ("nombre", "nombre contraparte", COD, *R, "Nombre de la contraparte."),
        ]),
        tb("ct-instrumento", "instrumento financiero", "treasury", "Instrumentos financieros negociados.", [
            ("id_instrumento", "identificador instrumento", ID, *PK, "Id de instrumento (PK)."),
            ("nombre", "nombre instrumento", COD, *R, "Nombre del instrumento."),
            ("tipo", "tipo instrumento", COD, *A, "Tipo (BONO/FX/SWAP)."),
        ]),
        tb("ct-posicion", "posicion tesoreria", "treasury", "Posiciones de tesorería por instrumento.", [
            ("id_posicion", "identificador posicion", ID, *PK, "Id de posición (PK)."),
            ("id_instrumento", "identificador instrumento", ID, *FK, "Instrumento (FK→instrumento)."),
            ("id_contraparte", "identificador contraparte", ID, *FKN, "Contraparte (FK→contraparte)."),
            ("monto", "monto posicion", IMP, *R, "Valor de la posición."),
            ("fecha", "fecha posicion", FEC, *P, "Fecha de la posición (partición)."),
        ]),
        tb("ct-limite-tesoreria", "limite tesoreria", "treasury", "Límites de exposición por contraparte.", [
            ("id_limite", "identificador limite", ID, *PK, "Id de límite (PK)."),
            ("id_contraparte", "identificador contraparte", ID, *FK, "Contraparte (FK→contraparte)."),
            ("limite", "limite tesoreria", IMP, *R, "Límite de exposición asignado."),
        ]),
    ]


def _table_id(spec_id: str) -> str:
    return spec_id


def _column_id(table_id: str, col_key: str) -> str:
    return f"{table_id}.{col_key}"


def build_tables() -> list[dict]:
    """`canonical_tables`: ~11 tablas físicas. `schema` es el alias persistido."""
    mappings = _column_mappings()
    out: list[dict] = []
    for spec in _tables_spec():
        out.append({
            "_id": _table_id(spec["id"]),
            "logicalName": spec["logical"],
            # nombre físico de tabla: scope 'table' (sin separador). Usamos el
            # mapa de columnas como diccionario base (los primes coinciden).
            "physicalName": physicalize(
                spec["logical"], mappings,
                separator=_TBL_NAMING["separator"], case=_TBL_NAMING["case"],
            ),
            "schema": spec["schema"],            # alias de sql_schema (persistido)
            "description": spec["desc"],
            "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
        })
    return out


def build_columns() -> list[dict]:
    """`canonical_columns`: columnas por tabla con PK/FK/nullable/partition,
    dataType derivado del parent domain, y físico vía el engine de naming."""
    mappings = _column_mappings()
    out: list[dict] = []
    for spec in _tables_spec():
        tid = _table_id(spec["id"])
        for ordinal, col in enumerate(spec["cols"]):
            col_key, logical, pdid, pk, fk, nullable, partition, desc = col
            out.append({
                "_id": _column_id(tid, col_key),
                "tableId": tid,
                "logicalName": logical,
                "physicalName": physicalize(
                    logical, mappings,
                    separator=_COL_NAMING["separator"], case=_COL_NAMING["case"],
                ),
                "parentDomainId": pdid,
                "dataType": _DOMAIN_TYPE.get(pdid, "STRING"),
                "typeOverridden": False,
                "isPrimaryKey": pk,
                "isForeignKey": fk,
                "isNullable": nullable,
                "isPartition": partition,
                "description": desc,
                "ordinal": ordinal,
                "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
            })
    return out


# ── Relationships (crow's-foot) ──────────────────────────────────────────────
# (id, sourceTable, sourceCol, targetTable, targetCol, srcCard, tgtCard, identifying)
# Convención: source = lado "muchos" (FK), target = lado "uno" (PK del padre).
_RELATIONSHIPS: list[tuple[str, str, str, str, str, str, str, bool]] = [
    ("rel-cuenta-cliente", "ct-cuenta", "id_cliente", "ct-cliente", "id_cliente",
     "many", "one", True),
    ("rel-cuenta-sucursal", "ct-cuenta", "id_sucursal", "ct-sucursal", "id_sucursal",
     "many", "zero-one", False),
    ("rel-cuenta-moneda", "ct-cuenta", "id_moneda", "ct-moneda", "id_moneda",
     "many", "one", False),
    ("rel-movimiento-cuenta", "ct-movimiento", "id_cuenta", "ct-cuenta", "id_cuenta",
     "many", "one", True),
    ("rel-tarjeta-cliente", "ct-tarjeta", "id_cliente", "ct-cliente", "id_cliente",
     "many", "one", True),
    ("rel-tarjeta-cuenta", "ct-tarjeta", "id_cuenta", "ct-cuenta", "id_cuenta",
     "many", "zero-one", False),
    ("rel-prestamo-cliente", "ct-prestamo", "id_cliente", "ct-cliente", "id_cliente",
     "many", "one", True),
    ("rel-prestamo-producto", "ct-prestamo", "id_producto", "ct-producto", "id_producto",
     "many", "zero-one", False),
    ("rel-empleado-sucursal", "ct-empleado", "id_sucursal", "ct-sucursal", "id_sucursal",
     "many", "zero-one", False),
    ("rel-riesgo-cliente", "ct-riesgo", "id_cliente", "ct-cliente", "id_cliente",
     "one", "one", True),
    ("rel-exposicion-cliente", "ct-exposicion", "id_cliente", "ct-cliente", "id_cliente",
     "many", "one", False),
    ("rel-exposicion-producto", "ct-exposicion", "id_producto", "ct-producto", "id_producto",
     "many", "zero-one", False),
    # — relaciones de las tablas extra (data dummy ampliada) —
    ("rel-lead-cliente", "ct-lead", "id_cliente", "ct-cliente", "id_cliente", "many", "zero-one", False),
    ("rel-lead-campania", "ct-lead", "id_campania", "ct-campania", "id_campania", "many", "one", True),
    ("rel-interaccion-campania", "ct-interaccion", "id_campania", "ct-campania", "id_campania", "many", "one", True),
    ("rel-puesto-departamento", "ct-puesto", "id_departamento", "ct-departamento", "id_departamento", "many", "one", True),
    ("rel-nomina-empleado", "ct-nomina", "id_empleado", "ct-empleado", "id_empleado", "many", "one", True),
    ("rel-asistencia-empleado", "ct-asistencia", "id_empleado", "ct-empleado", "id_empleado", "many", "one", True),
    ("rel-pago-cliente", "ct-pago", "id_cliente", "ct-cliente", "id_cliente", "many", "one", True),
    ("rel-pago-canal", "ct-pago", "id_canal", "ct-canal", "id_canal", "many", "one", False),
    ("rel-transferencia-pago", "ct-transferencia", "id_pago", "ct-pago", "id_pago", "many", "one", True),
    ("rel-beneficiario-cliente", "ct-beneficiario", "id_cliente", "ct-cliente", "id_cliente", "many", "zero-one", False),
    ("rel-usuario-cliente", "ct-usuario-digital", "id_cliente", "ct-cliente", "id_cliente", "many", "one", True),
    ("rel-sesion-usuario", "ct-sesion", "id_usuario", "ct-usuario-digital", "id_usuario", "many", "one", True),
    ("rel-dispositivo-usuario", "ct-dispositivo", "id_usuario", "ct-usuario-digital", "id_usuario", "many", "zero-one", False),
    ("rel-notificacion-usuario", "ct-notificacion", "id_usuario", "ct-usuario-digital", "id_usuario", "many", "one", False),
    ("rel-posicion-instrumento", "ct-posicion", "id_instrumento", "ct-instrumento", "id_instrumento", "many", "one", True),
    ("rel-posicion-contraparte", "ct-posicion", "id_contraparte", "ct-contraparte", "id_contraparte", "many", "zero-one", False),
    ("rel-limite-contraparte", "ct-limite-tesoreria", "id_contraparte", "ct-contraparte", "id_contraparte", "many", "one", True),
]


def build_relationships() -> list[dict]:
    """`relationships`: PK/FK con cardinalidades crow's-foot."""
    out: list[dict] = []
    for rid, st, sc, tt, tc, src_card, tgt_card, ident in _RELATIONSHIPS:
        out.append({
            "_id": rid,
            "sourceTableId": st,
            "sourceColumnId": _column_id(st, sc),
            "targetTableId": tt,
            "targetColumnId": _column_id(tt, tc),
            "sourceCardinality": src_card,
            "targetCardinality": tgt_card,
            "identifying": ident,
            "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
        })
    return out


# ── Views ────────────────────────────────────────────────────────────────────
def build_views() -> list[dict]:
    """`views`: un par de vistas SQL de ejemplo."""
    return [
        {"_id": "vw-cliente-saldo", "name": "VW_CLIENTE_SALDO",
         "sql": ("SELECT c.ID_CLI, c.NOM_CLI, SUM(a.SLD_CTA) AS SLD_TOTAL\n"
                 "FROM core.CLI c JOIN core.CTA a ON a.ID_CLI = c.ID_CLI\n"
                 "GROUP BY c.ID_CLI, c.NOM_CLI"),
         "description": "Saldo total agregado por cliente.",
         "flgactive": True, "createdAt": _now(), "updatedAt": _now()},
        {"_id": "vw-riesgo-alto", "name": "VW_RIESGO_ALTO",
         "sql": ("SELECT r.ID_CLI, r.SCO_RIESGO, r.MTO_DEU_USD\n"
                 "FROM risk.RIESGO r WHERE r.SCO_RIESGO < 400"),
         "description": "Clientes con score de riesgo bajo (alta exposición).",
         "flgactive": True, "createdAt": _now(), "updatedAt": _now()},
    ]


# ── Subject areas / canvases ─────────────────────────────────────────────────
# (id, projectId, folderId, name, [tableIds], {tableId: (x, y)})
def _canvases_spec() -> list[dict]:
    return [
        {
            "id": "sa-deposits", "project": "proj-core-banking", "folder": "fld-deposits",
            "name": "Deposits & Accounts",
            "tables": ["ct-cliente", "ct-cuenta", "ct-sucursal", "ct-moneda", "ct-movimiento"],
            "pos": {
                "ct-cliente": (80, 80), "ct-cuenta": (480, 80),
                "ct-sucursal": (80, 360), "ct-moneda": (480, 360),
                "ct-movimiento": (880, 200),
            },
        },
        {
            "id": "sa-lending", "project": "proj-core-banking", "folder": "fld-lending",
            "name": "Lending",
            "tables": ["ct-cliente", "ct-producto", "ct-prestamo"],
            "pos": {
                "ct-cliente": (80, 120), "ct-producto": (80, 420),
                "ct-prestamo": (520, 260),
            },
        },
        {
            "id": "sa-cards", "project": "proj-customer-360", "folder": None,
            "name": "Cards Overview",
            "tables": ["ct-cliente", "ct-cuenta", "ct-tarjeta", "ct-producto"],
            "pos": {
                "ct-cliente": (80, 80), "ct-cuenta": (480, 80),
                "ct-tarjeta": (480, 380), "ct-producto": (80, 380),
            },
        },
        {
            "id": "sa-risk", "project": "proj-risk-analytics", "folder": None,
            "name": "Credit Risk",
            "tables": ["ct-cliente", "ct-riesgo", "ct-exposicion", "ct-producto"],
            "pos": {
                "ct-cliente": (80, 200), "ct-riesgo": (520, 80),
                "ct-exposicion": (520, 400), "ct-producto": (940, 240),
            },
        },
        # — canvases de los proyectos extra (referencian las tablas nuevas) —
        {
            "id": "sa-marketing", "project": "proj-marketing", "folder": None,
            "name": "Marketing & Campaigns",
            "tables": ["ct-campania", "ct-lead", "ct-segmento", "ct-interaccion", "ct-cliente"],
            "pos": {
                "ct-campania": (80, 80), "ct-lead": (480, 80), "ct-segmento": (80, 360),
                "ct-interaccion": (480, 360), "ct-cliente": (900, 200),
            },
        },
        {
            "id": "sa-hr", "project": "proj-hr", "folder": None,
            "name": "People & Payroll",
            "tables": ["ct-departamento", "ct-puesto", "ct-nomina", "ct-asistencia", "ct-empleado"],
            "pos": {
                "ct-departamento": (80, 80), "ct-puesto": (480, 80), "ct-empleado": (900, 80),
                "ct-nomina": (300, 380), "ct-asistencia": (700, 380),
            },
        },
        {
            "id": "sa-payments", "project": "proj-payments", "folder": None,
            "name": "Payments & Transfers",
            "tables": ["ct-pago", "ct-transferencia", "ct-beneficiario", "ct-canal", "ct-cuenta"],
            "pos": {
                "ct-canal": (80, 80), "ct-pago": (480, 80), "ct-cuenta": (900, 80),
                "ct-transferencia": (300, 380), "ct-beneficiario": (700, 380),
            },
        },
        {
            "id": "sa-digital", "project": "proj-digital", "folder": None,
            "name": "Digital Channels",
            "tables": ["ct-usuario-digital", "ct-sesion", "ct-dispositivo", "ct-notificacion", "ct-cliente"],
            "pos": {
                "ct-cliente": (80, 200), "ct-usuario-digital": (480, 200),
                "ct-sesion": (900, 60), "ct-dispositivo": (900, 300), "ct-notificacion": (900, 540),
            },
        },
        {
            "id": "sa-treasury", "project": "proj-treasury", "folder": None,
            "name": "Treasury Positions",
            "tables": ["ct-posicion", "ct-instrumento", "ct-contraparte", "ct-limite-tesoreria"],
            "pos": {
                "ct-instrumento": (80, 80), "ct-contraparte": (80, 380),
                "ct-posicion": (520, 200), "ct-limite-tesoreria": (920, 200),
            },
        },
        {
            "id": "sa-fraud", "project": "proj-fraud", "folder": None,
            "name": "Fraud Signals",
            "tables": ["ct-pago", "ct-transferencia", "ct-cliente", "ct-cuenta"],
            "pos": {
                "ct-cliente": (80, 80), "ct-cuenta": (480, 80),
                "ct-pago": (80, 380), "ct-transferencia": (480, 380),
            },
        },
    ]


def build_subject_areas() -> list[dict]:
    """`subject_areas` (canvases): tableIds del pool + layout x/y + drawings."""
    out: list[dict] = []
    for spec in _canvases_spec():
        layout = {tid: {"x": float(x), "y": float(y)} for tid, (x, y) in spec["pos"].items()}
        out.append({
            "_id": spec["id"],
            "projectId": spec["project"],
            "folderId": spec["folder"],
            "name": spec["name"],
            "tableIds": list(spec["tables"]),
            "layout": layout,
            "drawings": [],
            "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
        })
    return out


# ── Changesets (versiones publicadas + requests en revisión) ─────────────────
def build_changesets() -> list[dict]:
    """`changesets`: v14 publicada (producción actual) + 2 requests en revisión.

    Los changesets son SOLO cabecera (estado/decisiones): los cambios viven en
    `changeset_changes` (ver `build_changeset_changes`), un doc por cambio.
    """
    # v14 — producción actual (approved). Sin cambios: refleja el publicado.
    v14 = {
        "_id": "cs-v14",
        "title": "Baseline modelo Core",
        "owner": "mr",
        "status": "approved",
        "description": "Versión base publicada del modelo (Core/Cards/Risk).",
        "versionLabel": "v14",
        "projectIds": ["proj-core-banking", "proj-customer-360", "proj-risk-analytics"],
        "reviewers": ["ana", "beto"],
        "approvals": {
            "ana": {"status": "approved", "note": "OK base", "at": _now()},
            "beto": {"status": "approved", "at": _now()},
        },
        "comments": [
            {"author": "mr", "text": "Publicada como línea base de producción.", "at": _now()},
        ],
        "createdAt": _now(), "updatedAt": _now(),
        "submittedAt": _now(), "reviewedBy": "beto", "reviewedAt": _now(),
        # Marcador de apply completo: approved SIN appliedAt = publish
        # interrumpido (current_production lo excluye).
        "appliedAt": _now(),
        "flgactive": True,
    }

    v15 = {
        "_id": "cs-v15",
        "title": "Garantías de préstamos",
        "owner": "ana",
        "status": "submitted",
        "description": "Nueva entidad `garantia` y FK desde `prestamo`.",
        "versionLabel": "v15",
        "projectIds": ["proj-core-banking"],
        "reviewers": ["beto", "mr"],
        "approvals": {
            "beto": {"status": "approved", "note": "Modelo correcto.", "at": _now()},
        },
        "comments": [
            {"author": "ana", "text": "Agrego garantías para el flujo de lending.", "at": _now()},
            {"author": "beto", "text": "Falta confirmar nulabilidad de la FK.", "at": _now()},
        ],
        "createdAt": _now(), "updatedAt": _now(), "submittedAt": _now(),
        "flgactive": True,
    }

    v16 = {
        "_id": "cs-v16",
        "title": "Atributos de cliente y tarjeta",
        "owner": "beto",
        "status": "submitted",
        "description": "Segmento de cliente + límite obligatorio en tarjeta.",
        "versionLabel": "v16",
        "projectIds": ["proj-customer-360", "proj-core-banking"],
        "reviewers": ["ana"],
        "approvals": {},
        "comments": [
            {"author": "beto", "text": "Revisar impacto del segmento en reporting.", "at": _now()},
        ],
        "createdAt": _now(), "updatedAt": _now(), "submittedAt": _now(),
        "flgactive": True,
    }

    return [v14, v15, v16]


def build_changeset_changes() -> list[dict]:
    """`changeset_changes`: los cambios de los requests en revisión, UN doc por
    cambio (`_id = {csId}::{collection}::{entityId}`, como escribe el repo).

    Upsertan tablas/columnas que referencian ids de tablas YA publicadas, para
    que el diff estructurado y el cálculo de impacto (tablas tocadas + afectadas
    vía relationships) tengan contenido.
    """
    # v15 — agrega tabla `garantia` (lending) + una columna nueva a `prestamo`
    # (toca ct-prestamo → impacto vía relationships).
    v15_changes = {
        "canonical_tables": {
            "ct-garantia": {"op": "upsert", "payload": {
                "id": "ct-garantia", "logicalName": "garantia prestamo",
                "physicalName": "GARANTIAPREST", "schema": "lending",
                "description": "Garantías asociadas a préstamos.",
            }},
        },
        "canonical_columns": {
            "ct-garantia.id_garantia": {"op": "upsert", "payload": {
                "id": "ct-garantia.id_garantia", "tableId": "ct-garantia",
                "logicalName": "identificador garantia", "physicalName": "ID_GARANTIA",
                "parentDomainId": "pd-identificador", "dataType": "BIGINT",
                "isPrimaryKey": True, "ordinal": 0,
            }},
            "ct-garantia.valor": {"op": "upsert", "payload": {
                "id": "ct-garantia.valor", "tableId": "ct-garantia",
                "logicalName": "monto garantia", "physicalName": "MTO_GARANTIA",
                "parentDomainId": "pd-importe", "dataType": "DECIMAL(18,2)", "ordinal": 1,
            }},
            "ct-prestamo.id_garantia": {"op": "upsert", "payload": {
                "id": "ct-prestamo.id_garantia", "tableId": "ct-prestamo",
                "logicalName": "identificador garantia", "physicalName": "ID_GARANTIA",
                "parentDomainId": "pd-identificador", "dataType": "BIGINT",
                "isForeignKey": True, "isNullable": True, "ordinal": 6,
            }},
        },
    }
    # v16 — edita `tarjeta` (límite) + agrega columna a `cliente`
    # (toca ct-cliente → muchas tablas afectadas vía relationships).
    v16_changes = {
        "canonical_columns": {
            "ct-tarjeta.limite": {"op": "upsert", "payload": {
                "id": "ct-tarjeta.limite", "tableId": "ct-tarjeta",
                "logicalName": "limite tarjeta", "physicalName": "LIM_TAR",
                "parentDomainId": "pd-importe", "dataType": "DECIMAL(18,2)",
                "isNullable": False, "ordinal": 4,
                "description": "Límite de crédito (ahora obligatorio).",
            }},
            "ct-cliente.segmento": {"op": "upsert", "payload": {
                "id": "ct-cliente.segmento", "tableId": "ct-cliente",
                "logicalName": "codigo segmento", "physicalName": "COD_SEGMENTO",
                "parentDomainId": "pd-codigo", "dataType": "STRING",
                "isNullable": True, "ordinal": 4,
                "description": "Segmento comercial del cliente.",
            }},
        },
    }
    out: list[dict] = []
    for cs_id, changes in (("cs-v15", v15_changes), ("cs-v16", v16_changes)):
        for collection, col_changes in changes.items():
            for eid, ch in col_changes.items():
                doc = {
                    "_id": f"{cs_id}::{collection}::{eid}",
                    "csId": cs_id,
                    "collection": collection,
                    "entityId": eid,
                    "op": ch["op"],
                    "at": _now(),
                    "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
                }
                if ch["op"] != "delete":
                    doc["payload"] = ch.get("payload") or {}
                out.append(doc)
    return out


# ── Auth: roles + users (RBAC data-driven, login usuario/contraseña) ─────────
from app.core.security import hash_password  # noqa: E402
from app.features.auth.models import PERMISSIONS  # noqa: E402

_DEMO_PASSWORD = "123456789"   # demo para todos los usuarios sembrados (mock 13)
# Passwords por usuario (override del demo). `admin` es el atajo de dev.
_PASSWORD_OVERRIDES: dict[str, str] = {"admin": "admin"}

# Matriz de permisos por rol (pantalla 14 + fila Data Standards, R10).
# `standards.edit` = solo Administrador en el seed (editable desde Admin).
_ROLE_GRANTS: dict[str, tuple[str, str, set[str]]] = {
    "administrador": ("Administrador", "Control total · gestiona usuarios", set(PERMISSIONS)),
    "modelador": ("Modelador", "Crea y edita tablas · envía a revisión",
                  {"model.view", "model.edit", "export"}),
    "revisor": ("Revisor", "Aprueba o rechaza solicitudes",
                {"model.view", "review.decide", "publish", "export"}),
    "lector": ("Lector", "Solo lectura · exporta metadata", {"model.view", "export"}),
}


def build_roles() -> list[dict]:
    out = []
    for key, (name, desc, grants) in _ROLE_GRANTS.items():
        out.append({
            "_id": key, "name": name, "description": desc,
            "permissions": {p: (p in grants) for p in PERMISSIONS},
            "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
        })
    return out


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return "?"
    return (parts[0][:2] if len(parts) == 1 else parts[0][0] + parts[-1][0]).upper()


# (username, name, email, role, projectIds[]|None=Todos, status)
_USERS: list[tuple] = [
    ("admin", "Admin", "admin@empresa.com", "administrador", None, "active"),   # atajo de dev · pass "admin"
    ("maria.rojas", "María Rojas", "maria.rojas@empresa.com", "administrador", None, "active"),
    ("juan.castillo", "Juan Castillo", "juan.castillo@empresa.com", "modelador",
     ["proj-core-banking", "proj-risk-analytics"], "active"),
    ("ana.lopez", "Ana López", "ana.lopez@empresa.com", "revisor", ["proj-core-banking"], "active"),
    ("diego.torres", "Diego Torres", "diego.torres@empresa.com", "lector", ["proj-customer-360"], "active"),
    ("sofia.paredes", "Sofía Paredes", "sofia.paredes@empresa.com", "lector", ["proj-risk-analytics"], "invited"),
    # Actores simulados del flujo de aprobación previo (para seguir probándolo).
    ("mr", "María Rey", "mr@empresa.com", "administrador", None, "active"),
    ("ana", "Ana Gomez", "ana@empresa.com", "revisor", None, "active"),
    ("beto", "Beto Diaz", "beto@empresa.com", "revisor", None, "active"),
    ("carla", "Carla Ruiz", "carla@empresa.com", "modelador", None, "active"),
    ("qa", "QA Tester", "qa@empresa.com", "revisor", None, "active"),
]


def build_users() -> list[dict]:
    """Usuarios con contraseña demo (hash bcrypt). `projectIds=[]` = todos."""
    out = []
    for username, name, email, role, project_ids, status in _USERS:
        out.append({
            "_id": username, "email": email, "name": name, "role": role,
            "projectIds": project_ids or [], "status": status,
            "initials": _initials(name), "passwordHash": hash_password(_PASSWORD_OVERRIDES.get(username, _DEMO_PASSWORD)),
            "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
        })
    return out


def build_standards_versions() -> list[dict]:
    """Baseline v1: snapshot del estado sembrado de estándares (dominios +
    diccionario + naming_config). Punto de retorno para rollbacks."""
    domains = [{"id": d["_id"], "name": d["name"], "defaultDataType": d["defaultDataType"],
                "namingTerm": d.get("namingTerm"), "description": d.get("description")}
               for d in build_parent_domains()]
    terms = [{"id": e["_id"], "term": e["term"], "abbrev": e["abbrev"],
              "scope": e["scope"], "wordType": e.get("wordType")}
             for e in build_glossary_terms()]
    naming = {d["_id"]: {"separator": d["separator"], "case": d["case"]}
              for d in build_naming_config()}
    snapshot = {"domains": domains, "dict": terms,
                "namingConfig": {"column": naming.get("column"), "table": naming.get("table")}}
    return [{
        "_id": "sv-v1", "seq": 1, "label": "v1", "kind": "baseline",
        "title": "Baseline · initial standards import", "description": None,
        "author": "system", "createdAt": _now(), "appliedAt": _now(), "status": "baseline",
        "diff": {"added": [], "edited": [], "removed": []},
        "impact": {"tables": 0, "columns": 0}, "snapshot": snapshot, "revertsSeq": None,
        "flgactive": True,
    }]


# ── Ensamblado completo ──────────────────────────────────────────────────────
def build_all() -> dict[str, list[dict]]:
    """Dataset completo {collection: [docs]} para `insert_many`. Puro."""
    return {
        "projects": build_projects(),
        "folders": build_folders(),
        "subject_areas": build_subject_areas(),
        "canonical_tables": build_tables(),
        "canonical_columns": build_columns(),
        "relationships": build_relationships(),
        "views": build_views(),
        "parent_domains": build_parent_domains(),
        "glossary_terms": build_glossary_terms(),
        "naming_config": build_naming_config(),
        "changesets": build_changesets(),
        "changeset_changes": build_changeset_changes(),
        "roles": build_roles(),
        "users": build_users(),
        "standards_versions": build_standards_versions(),
    }


# ── Persistencia (async, Motor) ──────────────────────────────────────────────
async def main() -> None:
    from app.core.db.client import connect, disconnect, get_db

    await connect()
    db = await get_db()

    # Guardrail: contar `column_catalog` ANTES (debe quedar idéntico después).
    before = await db[PROTECTED_COLLECTION].count_documents({})

    data = build_all()

    # 1) Wipe SOLO las colecciones del modeler (nunca `column_catalog`).
    #    `drop()` (no `delete_many`): remueve docs Y **índices legacy** — p.ej. un
    #    unique `username_1` de un backend previo choca con el esquema nuevo
    #    (usamos `_id` como username, sin campo `username`). Recreamos los índices
    #    correctos al final con `ensure_indexes`.
    assert PROTECTED_COLLECTION not in MODELER_COLLECTIONS, "no borrar column_catalog"
    for coll in MODELER_COLLECTIONS:
        await db[coll].drop()
    print(f"dropped {len(MODELER_COLLECTIONS)} modeler collections "
          f"(preserved '{PROTECTED_COLLECTION}')")

    # 2) Insert por colección.
    counts: dict[str, int] = {}
    for coll, docs in data.items():
        if docs:
            res = await db[coll].insert_many(docs)
            counts[coll] = len(res.inserted_ids)
        else:
            counts[coll] = 0

    print("seeded:")
    for coll in data:
        print(f"  {coll:<20} {counts[coll]}")

    # 3) Recrear los índices correctos (los `drop()` se llevaron los legacy).
    from app.core.db.indexes import ensure_indexes
    await ensure_indexes(db)
    print("indexes ensured")

    # 4) Guardrail: `column_catalog` intacta.
    after = await db[PROTECTED_COLLECTION].count_documents({})
    status = "OK" if before == after else "⚠️  MISMATCH"
    print(f"column_catalog: before={before} after={after}  [{status}]")

    await disconnect()


if __name__ == "__main__":
    asyncio.run(main())
