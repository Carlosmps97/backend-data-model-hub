# Migración Erwin (.xml) → Data Model Hub

Paquete para importar modelos exportados desde Erwin ("Save As XML", formato
nativo `<erwin xmlns="http://www.erwin.com/dm">`). Análisis de origen: doc
`plan-implementacion/12-ANALISIS-MIGRACION-ERWIN.md`.

## Flujo

**Recomendado (doc 54): el orquestador `scripts/run_migration.py`** — un solo
comando encadena toda la secuencia (dry-run por default):

```bash
# ONE-SHOT (DESTRUCTIVO): carpeta recursiva → primer deployment completo
#   quality (gate + glosario cruzado) → reset total → create_admin → migrate
#   por archivo (secuencial) → audit → data functions → arrange → versión v1
.venv/bin/python -m scripts.run_migration --folder "ruta/carpeta"            # plan
.venv/bin/python -m scripts.run_migration --folder "ruta/carpeta" --apply [--force]

# APPEND (no destructivo): suma UN archivo a la BD viva
.venv/bin/python -m scripts.run_migration --append "ruta/modelo.xml" --apply
```

Proyecto destino por archivo: capa 1 del `<Locator>` del Mart
(`Mart://Mart/<Proyecto>/<Dominio>/<Modelo>` — archivos del mismo proyecto
Mart migran como familia al MISMO proyecto); sin Locator → nombre del
archivo. En Databricks: `scripts/databricks/carga_erwin_notebook.py`.

Paso a paso manual (lo mismo que encadena el orquestador):

```bash
# 1) GATE DE CALIDAD (solo lectura, no toca la BD) — correr SIEMPRE primero
.venv/bin/python -m scripts.erwin_migration.quality "ruta/DDV - CPYBCA.xml" [--json rep.json]

# 2) DRY-RUN de la migración (muestra el plan; no escribe)
.venv/bin/python -m scripts.erwin_migration.migrate "ruta/DDV - CPYBCA.xml"

# 3) PILOTO (una sola subject area) y luego corrida completa
.venv/bin/python -m scripts.erwin_migration.migrate "ruta.xml" --only-sa CPYBCAPYM --apply
.venv/bin/python -m scripts.erwin_migration.migrate "ruta.xml" --apply

# 4) post-migración
.venv/bin/python -m scripts.audit_data_consistency        # validación (16 chequeos)
# layout ELK sobre los canvases migrados: scripts/arrange_all
```

Acepta VARIOS .xml en una corrida (`migrate a.xml b.xml ...`): cada modelo →
un proyecto; los estándares (glosario/dominios/defs UDP) se **reúsan por
clave natural**, así que la config duplicada entre archivos converge sola.

## Decisiones aplicadas (owner 2026-07-12 — doc 12 §8)

| Tema | Decisión |
|---|---|
| Tablas/vistas sin Hive_Database | schema **"No_Definido"** |
| Tablas homónimas (mismo esquema o colisión global doc 50) | se migran **TODAS** (política 2026-08-22): la más usada conserva el nombre; el resto lleva sufijo **`_DUPn`** con su contenido intacto — mapeo completo en el reporte; re-runs conservan el sufijo por `erwinLongId` |
| Columnas duplicadas en un objeto | se conserva la **1ª** (orden físico) |
| Vistas | se migran TODAS con **showOnCanvas=True** |
| Anotaciones de diagrama | se **descartan** |
| Índices (Key_Group IF*) | **no se migran** (pendiente feature web) |
| UDP | Entity→table, Attribute→column, Model→canvas; Logical/Physical homónimas se colapsan (gana Physical); niveles View/Key_Group/Relationship se omiten |
| Glosario | scope=column, wordType=None; término duplicado exacto → 1º; full outer join entre archivos — término nuevo se SUMA; **misma palabra con abreviatura distinta = conflicto detectado y reportado (gana la vigente, jamás se pisa)**; `quality a.xml b.xml` lo chequea pre-carga |
| Relaciones de subtipo (Type 9 + Subtype_Symbol) | se migran como **subcategorías** (doc 53): `subcategory=true` + `subtypeSymbolId` compartido por grupo |

## Mapeo

Modelo→`projects` · Subject Area→`folders` · ER_Diagram→`subject_areas`
(canvas, layout inicial en grilla) · Hive_Database→`schemas` (entidad,
`sch-<name>`) · Entity→`canonical_tables` · Attribute→`canonical_columns`
(isPk + `pkPosition` del Key_Group PK, `ordinal` físico, parentDomainId del
Parent_Domain_Ref, typeOverridden si difiere del default del dominio,
`isNullable`, `isPartition` del UDP Particion) · Relationship 2/7→
`relationships` (**v2, doc 19: UN doc por relación con TODOS sus pares** en
`pairs[]`, no uno por par; identifying=tipo 2; cardinalidad del padre desde
`Null_Option_Type`) · View→`views` (fuente por relación tipo 16, columnas
passthrough con castType si el tipo difiere; **+ `description` de vista y de
columna-de-vista (F5)** cuando difiere del origen físico) · Domain custom→
`parent_domains` · Glossary→`glossary_terms` · Property_Type→
`udp_definitions` (**allowedValues = lista EXPLÍCITA `tag_Udp_Values_List`**,
catálogo completo aunque un valor no se use; niveles no-Entity/Attribute/Model
—incl. Domain— se omiten).

## Garantías

- **Idempotente**: ids deterministas (uuid5 del Long_Id Erwin) — re-correr el
  mismo archivo o una versión nueva del mismo modelo actualiza en su sitio.
- **No pisa datos ajenos**: docs con la misma clave natural pero otro id
  (creados a mano) → estándares se reúsan, tablas/vistas se omiten con aviso.
- **Trazabilidad**: cada doc migrado lleva `migratedFrom:"erwin"` +
  `erwinLongId` (la API los ignora al leer, `extra="ignore"`).
- `--apply` obligatorio para escribir; el gate con ERRORs corta salvo `--force`.

## Pendientes de confirmar (owner)

- Si Erwin puede exportar UN .xml con todos los "proyectos", o vendrán N
  archivos (el paquete soporta ambos).
- Si UDP/Glosario/Parent Domain son cross o por archivo (la convergencia por
  clave natural cubre los dos casos, pero conviene confirmar que las
  definiciones no difieren entre archivos).
- Versionado del historial de standards: la migración escribe el estado
  inicial sin snapshot en `standards_versions` (el primer cambio desde la UI
  versiona desde ahí).

## Tests

```bash
.venv/bin/python -m pytest tests/erwin_migration -q
```


## Catálogo FIJO de UDPs (doc 61 ronda 2 — 2026-08-30)

Las definiciones UDP ya **no se derivan del XML**: `standard_udps.py` es el catálogo canónico
(Table/Column/View/Model; "Tipo de Vista" [Regular default, Personalizada] es el único de View).
`migrate` lo siembra completo SIEMPRE y solo asocia los VALORES del XML: match case/espacios-
insensitive + `ALIASES` de typos conocidos → grafía canónica; sin match → default (key no escrita;
muestra en `udp_values_unmatched` del reporte). `--keep-unused-udp-defs` quedó deprecado (no-op).
