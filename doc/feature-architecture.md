# Arquitectura por features (backend) y guía de colaboración

> Cómo está organizado `backend-data-model-hub` para que varias personas
> construyan distintas features en paralelo sin pisarse. Léelo antes de agregar
> código. Complementa a `doc/architecture.md` (runtime / flujo de datos).

## La forma — monolito modular (vertical slices)

`app/core/` es la **infraestructura compartida** (no depende de features).
`app/features/<x>/` es una **feature autocontenida** con su stack completo.
`api/main.py` es un **shim** de compatibilidad (`uvicorn api.main:app` sigue
vivo); el entrypoint canónico es **`app.main:app`**.

```
app/
  main.py                  # composition root: create_app() monta los routers
  core/
    config.py · logging.py · models.py (DOC_CONFIG · TagDoc · coerce_tags)
    db/        client.py (connect/disconnect/get_db) · indexes.py
    api/       envelope.py (ok({...}))
  features/<x>/
    router.py        # HTTP: parsea request, llama al service, envuelve con ok()
    service.py       # reglas de negocio (validación, orquestación)
    repository.py    # acceso a Cosmos (Motor async)
    schemas.py       # DTOs de request/response (Pydantic)
    models.py        # documentos Pydantic de la entidad (extra="ignore")
    __init__.py      # API PÚBLICA de la feature
```

> **MVP sin auth**: no hay features `auth`/`users` ni `core/security`. La app es
> abierta/anónima; los permisos vuelven al final como una feature nueva.

## Las dos reglas que lo mantienen limpio

1. **Cross-feature solo por el `__init__`.** Otra feature importa
   `from app.features.<x> import Algo` (modelos, repo), nunca un submódulo
   interno. Dentro de la feature, imports relativos (`from .service import ...`).
2. **El `router` lo monta el composition root.** El `__init__` exporta la API
   cross-feature (modelos/repo) pero **no** el `router`; `app/main.py` importa
   `app.features.<x>.router`. Así se evitan ciclos.

DAG de dependencias (sin ciclos):
`core ← projects ← canvas ← {metadata, excel_import, health} ← app/main`.
(`canvas` usa `from app.features.projects import get_project`.)

## Capas dentro de una feature

| Archivo | Rol | Regla |
|---|---|---|
| `router.py` | Solo HTTP: parseo, `ok(...)`. | Sin reglas de negocio ni DB directa. |
| `service.py` | Reglas de negocio, validación. | Levanta `HTTPException` con el contrato exacto. |
| `repository.py` | CRUD en Cosmos (Motor). | Único lugar que toca la DB. |
| `schemas.py` | DTOs HTTP (request/response). | — |
| `models.py` | Documentos persistidos (Pydantic). | `extra="ignore"` (round-trip invariant). |

## Cómo agregar una feature

1. Crea `app/features/<name>/` con los archivos (copia uno chico, p. ej. `metadata/`).
2. `models.py` (si persiste algo) · `repository.py` (CRUD) · `service.py` (reglas)
   · `schemas.py` (DTOs) · `router.py` (endpoints).
3. Reexporta en `__init__.py` **solo** lo que otras features necesiten.
4. Monta el router en `app/main.py` (`from app.features.<name>.router import router`).
5. Índices nuevos → `app/core/db/indexes.py`.

> Las features grandes que vienen (la **matriz de permisos** robusta; colaboración
> sin autoguardado / locking / versionado; vistas lógica/física; workspaces)
> entran como nuevas `app/features/<x>/` sin tocar las demás.

## Qué queda en `core` (no es una feature)

`config` · `logging` · `db` (cliente Motor + índices) · `api` (envelope `ok`) ·
`models` (`DOC_CONFIG` + `TagDoc` compartido). Si algo lo usan dos o más features
no relacionadas, va en `core`.

## Invariante de persistencia

El canvas escribe casi crudo (`canvas/repository._replace_entities`) y la lectura
re-valida con Pydantic `extra="ignore"`: **un campo nuevo persistido necesita
estar declarado en el `models.py` correspondiente**, o se descarta al releer.
