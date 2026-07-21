# Scripts del backend — kit XML → Lakebase

**Actualizado:** 2026-07-20 · Limpieza hecha: se retiraron seeds sintéticos,
backfills ya aplicados, one-shots históricos y la migración Cosmos→Lakebase
(recuperables del historial git). **Todo lo que queda corre contra Lakebase**
(`DB_BACKEND=lakebase`, conexión del `.env`). Todos se ejecutan **desde la
raíz del backend** con su `.venv`.

Guía detallada de la migración (flags, políticas, qué migra y qué no):
`doc/migracion-erwin.md`.

---

## 1 · Migrar un XML de Erwin (`scripts/erwin_migration/`)

| Paso | Script | ¿Qué hace? | ¿Toca la BD? |
|---|---|---|---|
| 1 | `quality` | **Gate de calidad del XML**: audita el archivo contra las reglas de la plataforma ANTES de cargar (ERROR/WARN/INFO; exit 1 si hay ERRORs) | No |
| 2 | `extract_standards` | **Resumen a archivos**: glosario, dominios y definiciones UDP → JSON/CSV | No |
| 3 | `extract_model` | **Resumen a archivos**: tablas, columnas, vistas, relaciones, canvases + `summary.json` con los conteos | No |
| 4 | `reset_for_migration` | Vacía modelo+estándares+governance para carga limpia. **Preserva** usuarios/roles, naming_config, `column_catalog` y audit_log | Sí (`--apply`) |
| 5 | `migrate` | **La carga real**: XML → colecciones publicadas (sin `--apply` = dry-run con plan) | Sí (`--apply`) |

```bash
# 1. Auditar el XML (siempre primero)
.venv/bin/python -m scripts.erwin_migration.quality "../folder_data/DDV - CPYBCA.xml"

# 2-3. Resúmenes en frío (opcional): ¿cuántas tablas/columnas/vistas/UDP trae?
.venv/bin/python -m scripts.erwin_migration.extract_standards "../folder_data/DDV - CPYBCA.xml" --csv
.venv/bin/python -m scripts.erwin_migration.extract_model     "../folder_data/DDV - CPYBCA.xml" --csv
#    → model_out/<xml>/summary.json y tables/columns/views/relationships.csv

# 4. (solo si REEMPLAZAS la BD) reset previo
.venv/bin/python -m scripts.reset_for_migration            # dry-run
.venv/bin/python -m scripts.reset_for_migration --apply

# 5. Migrar
.venv/bin/python -m scripts.erwin_migration.migrate "../folder_data/DDV - CPYBCA.xml"           # dry-run
.venv/bin/python -m scripts.erwin_migration.migrate "../folder_data/DDV - CPYBCA.xml" --apply
```

`erwin_parser.py` y `policies.py` son librerías internas de estos comandos
(no se ejecutan directo).

## 2 · Post-migración

| Script | ¿Qué hace? | ¿Cuándo? |
|---|---|---|
| `arrange_all` | Auto-arrange ELK de TODOS los canvases (tablas + vistas) | Siempre tras `migrate --apply` |
| `audit_data_consistency` | **Calidad de la data en BD**: chequeos C1–C10 contra las reglas de la plataforma (duplicados, huérfanos, fuentes rotas, particiones incongruentes…) | Siempre tras migrar (esperado: 0 fixables) |
| `create_admin` | Crea/actualiza el usuario `admin`/`admin` + los 4 roles (idempotente) | BD nueva, para poder entrar |
| `seed_ddl_export_rules` | **Ruleset base de DDL Export** (doc 30): 8 reglas (masking técnico, desencriptación de negocio `bcp_ddv_desencrypt`, tags, TBLPROPERTIES vacuum, cascada `_rej` + vista técnica) + lookups `vacuum_map`/`dac_map`, como UNA versión de Data Standards. Aborta si ya hay reglas | BD nueva, tras `create_admin` |
| `fix_particiones_ddv_20260717` | Re-aplica las **3 correcciones de partición decididas por el owner** (doc 21 §3). No están en el XML: re-migrar el DDV las pisa (C10 avisa) | Solo si re-migras `DDV - CPYBCA.xml` |

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
                                                            # e2e_estructura_versionada

# Recuperar un publish que murió a la mitad
.venv/bin/python scripts/reapply_changeset.py [changeset_id]
```

## 4 · Volver a la VERSIÓN BASE (`reset_to_base_version`)

**Deja la web solo con lo migrado del XML y el historial de versiones limpio:
UNA versión por módulo** — `v1 Base` en Model y `v1` (baseline) en Data
Standards. Úsalo cuando las pruebas (E2E o manuales) llenaron la web de
versiones/residuos y quieres volver al punto de partida.

Qué hace con `--apply`:
1. **Conserva** la versión base (`versionLabel="v1"`) y TODO lo migrado — no
   re-migra: la data publicada del XML y las correcciones quedan intactas.
2. **Deshace en producción** lo que aplicaron las versiones posteriores a v1
   (restaura entidades modificadas con su imagen previa, borra las creadas).
3. **Borra el registro de todas las demás versiones** (aplicadas, drafts,
   submitted, rejected) — el historial de la web queda solo con v1.
4. Crea el **baseline de Data Standards** si no existe y asegura el permiso
   `rollback` (administrador/revisor).

Después: cualquier versión nueva que publiques puede volver a la base con el
botón **Restore** sobre v1 (rollback normal de la plataforma) — sin scripts.

```bash
.venv/bin/python -m scripts.reset_to_base_version            # dry-run: muestra el plan
.venv/bin/python -m scripts.reset_to_base_version --apply    # ⚠️ DESTRUCTIVO
```

Requiere que exista el changeset `v1` (lo dejó la migración real; si no está,
aborta sin tocar nada). Preserva usuarios/roles, naming_config,
`column_catalog` y audit_log.

## Flujo completo (BD nueva en otro workspace)

```bash
.venv/bin/python -m scripts.erwin_migration.quality "modelo.xml"        # gate OK
.venv/bin/python -m scripts.erwin_migration.migrate "modelo.xml" --apply
.venv/bin/python scripts/arrange_all.py
.venv/bin/python scripts/create_admin.py
.venv/bin/python -m scripts.seed_ddl_export_rules --apply               # ruleset base de export
.venv/bin/python -m scripts.audit_data_consistency                      # esperado: 0
# solo si el XML es el DDV actual:
.venv/bin/python -m scripts.fix_particiones_ddv_20260717 --apply
```

> Para el flujo "rollback a la base" (§4) hace falta el changeset marcador
> `versionLabel="v1"` con 0 cambios. En la BD actual ya existe ("Base — Modelo
> DDV real"); en una BD recién migrada créalo una vez (versión vacía publicada)
> — `migrate` no lo crea porque escribe directo a publicado.

## Retirados (2026-07-20, en historial git)

`seed_modeler` · `seed_stress` · `seed_demo_precisiones` · `seed_ddv_synthetic` ·
`backfill_schemas` · `backfill_relationships_v2` · `backfill_cardinalidad_particion` ·
`backfill_udp_allowed_values` · `backfill_view_col_defs` (su lógica ya vive en
`migrate`) · `migrate_changes_to_collection` · `migrate_view_sources` ·
`migrate_view_source_tables` · `fix_udp_canvas_data` ·
`lakebase/` (migración Cosmos→Lakebase, ejecutada el 2026-07-19).
(`reset_to_base_version` se retiró y se RESTAURÓ el mismo día como herramienta
permanente, sin la dependencia del seed.)
