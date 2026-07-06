# Arquitectura por features (backend) y guía de colaboración

> Cómo está organizado `backend-data-model-hub` para que varias personas
> construyan distintas features en paralelo sin pisarse. Léelo antes de agregar
> código. (La visión de producto y los flujos viven en `plan-implementacion/`
> en la raíz del workspace; los docs viejos del backend pre-refactor se
> eliminaron en la limpieza 2026-07.)

## La forma — monolito modular (vertical slices)

`app/core/` es la **infraestructura compartida** (no depende de features).
`app/features/<x>/` es una **feature autocontenida** con su stack completo.
El entrypoint canónico es **`app.main:app`** (así lo corre `app.yaml`).

```
app/
  main.py                  # composition root: create_app() monta los routers
  core/
    config.py · logging.py · models.py (DOC_CONFIG · TagDoc · coerce_tags)
    db/        client.py (connect/disconnect/get_db) · indexes.py
    api/       envelope.py (ok({...}))
    identity/  principal simulado (X-Dev-User) — seam para OBO real
    naming/    engine de fisicalización (diccionario + naming_config)
    versioning/ overlay.py (overlay + summarize_diff, puros)
  features/<x>/
    router.py        # HTTP: parsea request, llama al service, envuelve con ok()
    service.py       # reglas de negocio (validación, orquestación)
    repository.py    # acceso a Cosmos (Motor async)
    schemas.py       # DTOs de request/response (Pydantic)
    models.py        # documentos Pydantic de la entidad (extra="ignore")
    __init__.py      # API PÚBLICA de la feature
```

Features vigentes: `health · identity · domains · dictionary · catalog ·
changesets (+ versions/requests) · projects (+ subject areas) · folders ·
relationships · views · summary · reporting · settings`.

> **MVP sin auth**: no hay features `auth`/`users` ni `core/security`. La app es
> abierta/anónima; los permisos vuelven al final como una feature nueva.

## Las dos reglas que lo mantienen limpio

1. **Cross-feature solo por imports de módulo público.** Otra feature importa
   `from app.features.<x> import repository` (o modelos), nunca detalles
   internos. Dentro de la feature, imports relativos (`from .service import ...`).
2. **El `router` lo monta el composition root.** `app/main.py` importa
   `app.features.<x>.router`. Así se evitan ciclos.

Guardrails automáticos: `tests/architecture/test_store_boundary.py` (capas) y
`tests/architecture/test_no_legacy_features.py` (las features/árboles legacy
no vuelven).

## Capas dentro de una feature

| Archivo | Rol | Regla |
|---|---|---|
| `router.py` | Solo HTTP: parseo, `ok(...)`. | Sin reglas de negocio ni DB directa. |
| `service.py` | Reglas de negocio, validación. | Funciones puras testeables + orquestación async. |
| `repository.py` | CRUD en Cosmos (Motor). | Único lugar que toca la DB. |
| `schemas.py` | DTOs HTTP (request/response). | — |
| `models.py` | Documentos persistidos (Pydantic). | `extra="ignore"` (round-trip invariant). |

## Cómo agregar una feature

1. Crea `app/features/<name>/` con los archivos (copia una chica, p. ej. `views/`).
2. `models.py` (si persiste algo) · `repository.py` (CRUD) · `service.py` (reglas)
   · `schemas.py` (DTOs) · `router.py` (endpoints).
3. Reexporta en `__init__.py` **solo** lo que otras features necesiten.
4. Monta el router en `app/main.py` (`from app.features.<name>.router import router`).
5. Índices nuevos → `app/core/db/indexes.py` (ojo: Cosmos RU **rechaza `.sort()`
   sobre campos sin índice** — o indexás el campo, o ordenás en Python).

## Qué queda en `core` (no es una feature)

`config` · `logging` · `db` (cliente Motor + índices) · `api` (envelope `ok`) ·
`identity` · `naming` · `versioning` · `models` (`DOC_CONFIG` + `TagDoc`).
Si algo lo usan dos o más features no relacionadas, va en `core`.

## Invariantes clave

- **Persistencia round-trip**: la lectura re-valida con Pydantic
  `extra="ignore"` — **un campo nuevo persistido necesita estar declarado en el
  `models.py` correspondiente**, o se descarta al releer.
- **Cambios de changeset**: viven en la colección `changeset_changes` (un doc
  por cambio, `_id = {csId}::{collection}::{entityId}`); se leen SOLO vía
  `changesets.repository.changes_map`. El doc de `changesets` es cabecera
  (estado/decisiones) — nunca vuelvas a embeber `changes` (límite 2MB/doc de
  Cosmos RU).
- **Lecturas de colecciones versionadas a escala**: siempre con slice/filtro
  (`repository.published(col, flt, limit)`); nada de "traer todo y filtrar".
