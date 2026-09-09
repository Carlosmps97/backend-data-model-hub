# Scripts del backend — kit XML → Lakebase

**Actualizado:** 2026-09-08 (doc 75: proyectos independientes) · **Todo corre
contra Lakebase** (conexión del `.env`), **desde la raíz del backend** con su
`.venv`.

Guía detallada de la migración (flags, políticas, qué migra y qué no):
`doc/migracion-erwin.md`. Un modelo Erwin llega partido en ~15 archivos: el
kit hace **merge incremental** (adopción por clave natural, conflictos por
score de uso, dedup de FKs repetidas, fusión de folders/canvases homónimos)
y escribe por LOTES (`bulk_write`) — un XML de 1.8 GB carga en minutos, no
horas. Cada `--apply` deja su reporte en `migration-reports/`.

---

## 1 · Migrar un XML de Erwin (`scripts/erwin_migration/`)

| Paso | Script | ¿Qué hace? | ¿Toca la BD? |
|---|---|---|---|
| 1 | `quality` | **Gate 1 — el archivo en frío**: audita contra las reglas de la plataforma (ERROR/WARN/INFO; exit 1 si hay ERRORs) | No |
| 2 | `crosscheck` | **Gate 2 — el archivo contra la BD** (y entre archivos): solapes de tablas/vistas/schemas con veredicto ADOPTA/ACTUALIZA (score), estándares (enums que crecen, defs A4), estimación de carga | Solo lee |
| 3 | `extract_standards` / `extract_model` | **Resúmenes a archivos** (JSON/CSV): glosario/dominios/UDP · tablas/columnas/vistas/relaciones/canvases | No |
| 4 | `reset_for_migration` | Reset DESTRUCTIVO: DROPea todas las tablas del schema `dmh` (modelo, estándares, governance, usuarios, roles, auditoría); el one-shot vuelve a crear `admin` y los roles | Sí (`--apply`) |
| 5 | `migrate` | **La carga real** (merge multi-archivo + lotes) dentro de UN proyecto. Sin `--apply` = dry-run. `--project` obligatorio si el proyecto ya existe; `--description` si se crea | Sí (`--apply`) |

```bash
# 1. Gate 1: auditar el XML (siempre primero)
.venv/bin/python -m scripts.erwin_migration.quality "../folder_data/modelo.xml"

# 2. Gate 2: cruzarlo contra la BD viva DEL PROYECTO (y contra otros XML si pasas varios)
.venv/bin/python -m scripts.erwin_migration.crosscheck "../folder_data/modelo.xml" --project "Familia" [--json out.json]

# 3. Resúmenes en frío (opcional)
.venv/bin/python -m scripts.erwin_migration.extract_standards "../folder_data/modelo.xml" --csv
.venv/bin/python -m scripts.erwin_migration.extract_model     "../folder_data/modelo.xml" --csv

# 4. (solo si REEMPLAZAS la BD) reset previo
.venv/bin/python -m scripts.reset_for_migration --apply

# 5. Migrar (los archivos de una familia comparten proyecto — R8; el one-shot lo deduce de la carpeta)
.venv/bin/python -m scripts.erwin_migration.migrate "../folder_data/modelo.xml" --project "Familia"           # dry-run
.venv/bin/python -m scripts.erwin_migration.migrate "../folder_data/modelo.xml" --project "Familia" --apply
#   flags: --description "…" · --report ruta.json · --keep-unused-udp-defs · --only-sa SA · --force
```

`erwin_parser.py` y `policies.py` son librerías internas de estos comandos
(no se ejecutan directo).

## 2 · Post-migración

| Script | ¿Qué hace? | ¿Cuándo? |
|---|---|---|
| `arrange_all` | Auto-arrange ELK de canvases (tablas + vistas). **`--project "X"` limita al proyecto recién cargado** (no pisa layouts de otros) | Siempre tras `migrate --apply` |
| `audit_data_consistency` | **Calidad de la data en BD**: chequeos C1–C10 contra las reglas de la plataforma (duplicados, huérfanos, fuentes rotas, particiones incongruentes…) | Siempre tras migrar (esperado: 0 fixables) |
| `create_admin` | Cuenta local `admin` (contraseña en el script) + 4 roles + whitelist SSO de Modeladores (idempotente) | BD nueva, para poder entrar |
| `mark_base_version` | **Marca lo cargado como versión base DE CADA PROYECTO**: changeset marcador `v1` (approved, 0 cambios — sin él la web bloquea Model), baseline de Data Standards del proyecto si su stream está vacío y permiso `rollback`. Idempotente; un proyecto con versiones aplicadas no recibe marcador | UNA vez, al FINAL de la carga completa (después del último XML) |
| `seed_ddl_export_rules` | **Ruleset base de DDL Export** (doc 76, los 7 DDL de la macro BCP) POR PROYECTO (`--project "X"` o `--all-projects`): 14 reglas (DROP comentado, TBLPROPERTIES vacuum, tags `updateFrecuency`/`isDAC`/`DAC`, tabla `_rej`, vistas técnicas y de rechazos NoDAC/DAC, desencriptación `decrypt_column_view` en vistas DAC y de negocio) + lookups `vacuum_map`/`update_frequency_map`/`dac_flag_map`/`dac_map`, como UNA versión de Data Standards del proyecto. Salta los proyectos que ya tienen reglas | BD nueva, tras `create_admin` |
| `fix_particiones_ddv_20260717` | Re-aplica las **3 correcciones de partición decididas por el owner** (doc 21 §3) en el proyecto del DDV (`--project`, default «Modelo DDV»). No están en el XML: re-migrar el DDV las pisa (C10 avisa) | Solo si re-migras `DDV - CPYBCA.xml` |

```bash
.venv/bin/python scripts/arrange_all.py                     # requiere node + elkjs (repo front hermano)
.venv/bin/python -m scripts.audit_data_consistency          # reporte
.venv/bin/python -m scripts.audit_data_consistency --fix    # aplica los fixes
.venv/bin/python scripts/create_admin.py
.venv/bin/python -m scripts.seed_ddl_export_rules --apply   # ruleset base (dry-run sin --apply)
.venv/bin/python -m scripts.fix_particiones_ddv_20260717 --apply   # solo XML DDV actual
```

## 3 · Validación y operación

| Script | ¿Qué hace? |
|---|---|
| `e2e/` | Suite E2E contra el backend **en vivo** (`E2E_BASE`, default `localhost:8000`): login real por rol → flujos completos vía HTTP. Auto-limpia lo que crea (los escenarios de rollback dejan registros de versión — limpiar con §4) |
| `reapply_changeset` | Recuperación: re-aplica un publish interrumpido (changeset `approved` sin `appliedAt`). Idempotente |
| `ci/tf-provider-mirror.sh` | Solo CI (GitHub Actions): mirror del provider terraform para el deploy del bundle |

```bash
# E2E (backend corriendo)
.venv/bin/python -m scripts.e2e.run_e2e all                 # todos los escenarios
.venv/bin/python -m scripts.e2e.run_e2e s02_version_lifecycle
.venv/bin/python scripts/e2e/e2e_schemas.py                 # suites específicas: e2e_relationships,
                                                            # e2e_rollback, e2e_views_versionadas,
                                                            # e2e_estructura_versionada, e2e_sso
                                                            # (login SSO + whitelist, doc 38)

# Recuperar un publish que murió a la mitad
.venv/bin/python scripts/reapply_changeset.py [changeset_id]
```

## 4 · Volver a la VERSIÓN BASE (`reset_to_base_version`)

**Deja un proyecto (o todos) solo con lo migrado del XML y el historial de
versiones limpio: UNA versión por módulo** — `v1 Base` en Model y `v1`
(baseline) en Data Standards del proyecto. Úsalo cuando las pruebas (E2E o
manuales) llenaron la web de versiones/residuos y quieres volver al punto de
partida. Doc 75: las versiones son por proyecto, así que el reset también
(`--project "X"`; sin él, todos los proyectos, uno por uno).

Qué hace con `--apply`, por proyecto:
1. **Conserva** la versión base (`versionLabel="v1"`) y TODO lo migrado — no
   re-migra: la data publicada del XML y las correcciones quedan intactas.
2. **Deshace en producción** lo que aplicaron las versiones posteriores a v1
   (restaura entidades modificadas con su imagen previa, borra las creadas).
3. **Borra el registro de todas las demás versiones del proyecto** (aplicadas,
   drafts, submitted, rejected) — el historial de la web queda solo con v1.
4. Crea el **baseline de Data Standards** del proyecto si no existe y asegura
   el permiso `rollback` (administrador/revisor).

Después: cualquier versión nueva que publiques puede volver a la base con el
botón **Restore** sobre v1 (rollback normal de la plataforma) — sin scripts.

```bash
.venv/bin/python -m scripts.reset_to_base_version                            # dry-run, todos los proyectos
.venv/bin/python -m scripts.reset_to_base_version --project "Modelo DDV" --apply   # ⚠️ DESTRUCTIVO (ese proyecto)
```

Requiere que exista el changeset `v1` del proyecto (lo dejó la migración
real; si no está, ese proyecto se salta sin tocar nada). Preserva
usuarios/roles, audit_log y los demás proyectos.

## Flujo completo (BD nueva en otro workspace)

```bash
# Recomendado: el orquestador, que deduce los proyectos de la estructura de la carpeta (doc 54 + doc 77)
.venv/bin/python -m scripts.run_migration --folder ../folder_data                 # plan
.venv/bin/python -m scripts.run_migration --folder ../folder_data --apply --force

# Paso a paso (lo mismo que encadena el orquestador):
.venv/bin/python -m scripts.erwin_migration.quality "modelo.xml"        # gate OK
.venv/bin/python -m scripts.erwin_migration.migrate "modelo.xml" --project "Mi proyecto" --apply
.venv/bin/python scripts/arrange_all.py
.venv/bin/python scripts/create_admin.py
.venv/bin/python -m scripts.seed_ddl_export_rules --all-projects --apply   # ruleset base por proyecto
.venv/bin/python -m scripts.audit_data_consistency                      # esperado: 0
.venv/bin/python -m scripts.mark_base_version --apply                   # v1 + baseline por proyecto + rollback
# solo si el XML es el DDV actual:
.venv/bin/python -m scripts.fix_particiones_ddv_20260717 --apply --project "Modelo DDV"
```

> El marcador `versionLabel="v1"` (0 cambios) es OBLIGATORIO en cada proyecto
> tras migrar a una BD nueva: `migrate` escribe directo a publicado sin crear
> versiones, y sin una versión aplicada la web bloquea el módulo Model de ese
> proyecto (huevo-y-gallina: el diálogo "Open model" exige producción
> publicada). Lo crea `mark_base_version --apply` al final de la carga
> completa. En Databricks: `scripts/databricks/carga_erwin_notebook.py` §13.

## Proyectos (doc 75, 2026-09-08)

Los proyectos son **independientes**: cada doc del modelo y de los estándares
lleva `projectId`, los ids de plataforma van namespaceados por proyecto
(`uuid5("<projectId>|<Long_Id de Erwin>")`), la unicidad del nombre físico de
tabla es por proyecto y los estándares (glosario, dominios, defs UDP, naming,
reglas DDL) y las versiones se siembran y se marcan **por proyecto**.

- **Proyecto destino por archivo** = la CONVENCIÓN del doc 77: cada subcarpeta
  de la raíz del one-shot es un proyecto que se llama como ella y fusiona sus
  `.xml` (`MODELO DDV/*.xml` → «MODELO DDV»); cada `.xml` suelto en la raíz es
  su propio proyecto con el nombre del archivo (`UDV INT FISICO.xml` → «UDV INT
  FISICO»). Dos orígenes que caen al mismo nombre ignorando mayúsculas =
  `DiscoveryError` antes de tocar la BD. El manifiesto `projects.json` de la D9
  del doc 75 quedó DEROGADO (si aparece uno, se ignora con un aviso).
- **Paralelismo por proyecto** (`--jobs`, default 4): gates y migrate corren en
  carriles, uno por proyecto; los archivos de un mismo proyecto siempre en
  orden (el prefetch `_DUPn` lo exige). `--jobs 1` = todo secuencial.
- **Estándares de un proyecto con varios archivos** = unión DISTINTA: el
  primero gana; abreviatura o tipo de dominio distinto en otro archivo → al
  reporte (`glossary_conflicts`, `domain_conflicts`; hoja «Dominios en
  conflicto» de `migration_detail_report`).
- Flags nuevos: `migrate --description`, `seed_ddl_export_rules
  --project|--all-projects`, `crosscheck --project`, `reset_to_base_version
  --project`, `fix_particiones_ddv_20260717 --project`.

## Retirados (2026-07-20, en historial git)

`seed_modeler` · `seed_stress` · `seed_demo_precisiones` · `seed_ddv_synthetic` ·
`backfill_schemas` · `backfill_relationships_v2` · `backfill_cardinalidad_particion` ·
`backfill_udp_allowed_values` · `backfill_view_col_defs` (su lógica ya vive en
`migrate`) · `migrate_changes_to_collection` · `migrate_view_sources` ·
`migrate_view_source_tables` · `fix_udp_canvas_data` ·
`lakebase/` (utilería de carga inicial de datos a Lakebase, retirada).
(`reset_to_base_version` se retiró y se RESTAURÓ el mismo día como herramienta
permanente, sin la dependencia del seed.)

## Retirados (2026-09-07, doc 75 D14)

`backfill_canvas_views` · `backfill_schema_kind` · `backfill_udp_view` ·
`homologate_domain_types` · `purge_invalid_relationships` · `seed_view_type_udp`:
todos superados por el one-shot (regla de la casa: nada de backfill/refix — lo
que sale del XML lo pone el kit y se re-migra) o por `audit_data_consistency --fix`.
