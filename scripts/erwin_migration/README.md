# Migración Erwin (.xml) → Data Model Hub

Paquete para importar modelos exportados desde Erwin ("Save As XML", formato
nativo `<erwin xmlns="http://www.erwin.com/dm">`). Análisis de origen: doc
`plan-implementacion/12-ANALISIS-MIGRACION-ERWIN.md`.

## Flujo

**Recomendado (doc 54): el orquestador `scripts/run_migration.py`** — un solo
comando encadena toda la secuencia (dry-run por default):

```bash
# ONE-SHOT (DESTRUCTIVO): carpeta recursiva → primer deployment completo
#   quality POR PROYECTO (gate + glosario cruzado) → reset total → create_admin →
#   migrate por archivo (secuencial) → audit → data functions por proyecto →
#   arrange → versión v1 de cada proyecto
.venv/bin/python -m scripts.run_migration --folder "ruta/carpeta"            # plan
.venv/bin/python -m scripts.run_migration --folder "ruta/carpeta" --apply [--force]

# APPEND (no destructivo): suma UN archivo a la BD viva
.venv/bin/python -m scripts.run_migration --append "ruta/modelo.xml" --apply
```

Proyecto destino por archivo (doc 77 §3): lo decide la **ubicación**, no un
manifiesto. Cada **subcarpeta** de la raíz es un proyecto que se llama como
ella y FUSIONA sus `.xml` (`MODELO DDV/*.xml` → «MODELO DDV»); cada `.xml`
**suelto en la raíz** es su propio proyecto con el nombre del archivo
(`UDV INT FISICO.xml` → «UDV INT FISICO»). Dos orígenes que caen al mismo
nombre ignorando mayúsculas = `DiscoveryError` antes de tocar la BD. Del
`<Locator>` del Mart sólo se toman dominio y modelo como origen informativo.
Los proyectos migran en **carriles paralelos** (`--jobs`, default 4; los
archivos de un mismo proyecto siempre en orden). En Databricks:
`scripts/databricks/carga_erwin_notebook.py`.

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

Acepta VARIOS .xml en una corrida (`migrate a.xml b.xml ... --project P`):
todos al MISMO proyecto. Dentro de un proyecto los estándares
(glosario/dominios/defs UDP) se **reúsan por clave natural** — unión
DISTINTA entre sus archivos: el primero gana y una discrepancia (abreviatura
o tipo de dominio distinto) va al reporte (`glossary_conflicts`,
`domain_conflicts`). Entre proyectos NADA se comparte (doc 75).

## Decisiones aplicadas (owner 2026-07-12 — doc 12 §8)

| Tema | Decisión |
|---|---|
| Tablas/vistas sin Hive_Database | schema **"No_Definido"** |
| Tablas homónimas (mismo esquema o colisión global doc 50) | se migran **TODAS** (política 2026-08-22): la más usada conserva el nombre; el resto lleva sufijo **`_DUPn`** con su contenido intacto — mapeo completo en el reporte; re-runs conservan el sufijo por `erwinLongId` |
| Columnas duplicadas en un objeto | se conserva la **1ª** (orden físico) |
| Vistas | se migran TODAS con **showOnCanvas=True** (flag informativo desde el doc 70) y son **miembros del canvas donde Erwin las dibuja** (`subject_areas.viewIds`, doc 70) — importar una tabla a otro canvas ya no arrastra sus vistas |
| Anotaciones de diagrama | se **descartan** |
| Índices (Key_Group IF*) | **no se migran** (pendiente feature web) |
| UDP | Entity→table, Attribute→column, Model→canvas, View→view; **cada faceta Logical/Physical es una def propia** (doc 69: clave `level\|view\|name`, ids `udpfix\|level\|[logical\|]name`) — los valores de ambas facetas van al MISMO `udpValues`; niveles Key_Group/Relationship se omiten |
| Glosario | scope=column, wordType=None; término duplicado exacto → 1º; full outer join entre archivos — término nuevo se SUMA; **misma palabra con abreviatura distinta = conflicto detectado y reportado (gana la vigente, jamás se pisa)**; `quality a.xml b.xml` lo chequea pre-carga |
| Relaciones de subtipo (Type 9 + Subtype_Symbol) | se migran como **subcategorías** (doc 53): `subcategory=true` + `subtypeSymbolId` compartido por grupo |

## Mapeo

Modelo→`projects` · Subject Area→`folders` · ER_Diagram→`subject_areas`
(canvas, layout inicial en grilla) · Hive_Database→`schemas` (entidad,
`sch-<name>`) · Entity→`canonical_tables` · Attribute→`canonical_columns`
(isPk + `pkPosition` del Key_Group PK, `ordinal` único — ver «Orden único de columnas», parentDomainId del
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

- **Idempotente**: ids deterministas y NAMESPACEADOS por proyecto (uuid5 de
  `<projectId>|<Long_Id Erwin>`) — re-correr el mismo archivo o una versión
  nueva del mismo modelo actualiza en su sitio; el mismo GUID en otro
  proyecto es otro documento.
- **No pisa datos ajenos**: docs con la misma clave natural pero otro id
  (creados a mano) → estándares se reúsan, tablas/vistas se omiten con aviso.
- **Trazabilidad**: cada doc migrado lleva `migratedFrom:"erwin"` +
  `erwinLongId` (la API los ignora al leer, `extra="ignore"`).
- `--apply` obligatorio para escribir; el gate con ERRORs corta salvo `--force`.

## Pendientes de confirmar (owner)

- Si Erwin puede exportar UN .xml con todos los "proyectos", o vendrán N
  archivos (el paquete soporta ambos).
- UDP/Glosario/Parent Domain: DECIDIDO (doc 75) — son por proyecto, unión
  distinta entre los archivos del proyecto; las discrepancias van al reporte.
- Versionado del historial de standards: la migración escribe el estado
  inicial sin snapshot en `standards_versions` (el primer cambio desde la UI
  versiona desde ahí).

## Tests

```bash
.venv/bin/python -m pytest tests/erwin_migration -q
```


## Catálogo FIJO de UDPs (doc 61 ronda 2 — 2026-08-30; facetas doc 69 — 2026-09-05)

Las definiciones UDP ya **no se derivan del XML**: `standard_udps.py` es el catálogo canónico —
**25 definiciones con faceta `view`**: Entity·Logical 6 · Table·Physical 8 · Attribute·Logical 2 ·
Column·Physical 5 · View 2 («Tipo de Vista», «Filtro Despliegue 2021») · Model 2. Las homónimas de
ambas facetas («Clasificacion del Dato») son defs DISTINTAS: las físicas conservan el id histórico
`udpfix|level|name`; las lógicas usan `udpfix|level|logical|name`.
`migrate` lo siembra completo SIEMPRE y solo asocia los VALORES del XML: match case/espacios-
insensitive + `ALIASES` de typos conocidos → grafía canónica; sin match → default (key no escrita;
muestra en `udp_values_unmatched` del reporte). `--keep-unused-udp-defs` quedó deprecado (no-op).

### Facetas lógico/físico (doc 69)

Erwin guarda UN objeto con dos vistas; la plataforma también (contrato `app/core/facets.py`). El kit
captura por columna `logicalDataType` (Logical_Data_Type), `logicalOnly`/`physicalOnly` (Is_*_Only) y por
dominio `defaultDataType` = **Physical_Data_Type** (antes se
sembraba el lógico) + `logicalDataType`. El override de tipo se evalúa **por faceta**: `typeOverridden` =
físico de la columna ≠ físico del dominio; `logicalTypeOverridden` = lógico ≠ lógico (canonizados) — antes
se comparaba físico vs lógico y ~20k columnas quedaban como falsos overrides.

### Orden único de columnas (doc 74)

Erwin guarda por entidad tres órdenes de atributos/columnas (Attribute order, Column order y Physical
order) más el de la llave; la plataforma maneja **UN solo orden** (`ordinal`, el mismo en el modelo lógico y
el físico). El kit lo hereda como el «físico normal» del owner (`policies.column_order`): **las llaves
primarias primero, en el orden de la llave** (`Key_Group_Members_Order_Ref_Array`, que además es
`pkPosition`), y después el resto en el **Column order** de Erwin (`Columns_Order_Ref_Array` — lo que el
diagrama y el Table Column Editor muestran por default; sin array cae al Attribute order y, sin éste, al
`Physical_Order`). El orden físico de la BD (`Physical_Columns_Order_Ref_Array`) sólo ordena los atributos
para deduplicar homónimas. Los rangos se calculan sobre las columnas conservadas (0..n-1) y las marcas de
partición se evalúan sobre ese mismo orden. Todo se materializa al correr el one-shot
(`run_migration.py --folder … --apply`) o el `--append` de un archivo: no hay backfills aparte (política del
owner: lo que se aprende del XML se incorpora al kit y se re-ejecuta).
