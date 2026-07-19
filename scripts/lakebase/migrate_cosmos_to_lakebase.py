"""Migración de DATOS Cosmos DB (Mongo) → Lakebase Postgres (doc 28 §7).

Copia TODAS las colecciones de la base Mongo (`COSMOS_DATABASE`) a tablas
`(id, doc jsonb)` del schema `LAKEBASE_PGSCHEMA`, canonicalizando BSON→JSON
(ObjectId→str; datetime→ISO — la app ya guarda strings ISO, es red de
seguridad). Cosmos queda INTACTO (solo lectura).

    .venv/bin/python -m scripts.lakebase.migrate_cosmos_to_lakebase           # dry-run
    .venv/bin/python -m scripts.lakebase.migrate_cosmos_to_lakebase --apply   # TRUNCATE + carga

Con `--apply`: crea schema/tablas/índices si faltan, TRUNCATE de cada tabla
destino y carga batched (idempotente: re-correr = re-copiar). Después correr
`scripts.lakebase.verify_migration`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv

BATCH = 1000


def canon(value):
    """BSON → JSON-safe, determinista (compartido con verify_migration)."""
    if isinstance(value, dict):
        return {str(k): canon(v) for k, v in value.items()}
    if isinstance(value, list):
        return [canon(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "isoformat"):  # datetime/date de BSON
        return value.isoformat()
    from bson import ObjectId

    if isinstance(value, ObjectId):
        return str(value)
    from bson import Decimal128

    if isinstance(value, Decimal128):
        return float(value.to_decimal())
    raise TypeError(f"Tipo BSON no soportado en la migración: {type(value)!r}")


async def run(apply: bool) -> int:
    from motor.motor_asyncio import AsyncIOMotorClient

    from app.core.config import settings

    if not settings.COSMOS_CONNECTION_STRING:
        print("ERROR: COSMOS_CONNECTION_STRING no está seteado (origen).")
        return 2
    mongo = AsyncIOMotorClient(settings.COSMOS_CONNECTION_STRING, serverSelectionTimeoutMS=15_000)
    src = mongo[settings.COSMOS_DATABASE]
    names = sorted(await src.list_collection_names())
    tag = "APPLY" if apply else "DRY-RUN"
    print(f"[{tag}] Cosmos `{settings.COSMOS_DATABASE}` → Lakebase "
          f"`{settings.PGDATABASE}`.{settings.LAKEBASE_PGSCHEMA} · {len(names)} colecciones")

    counts: dict[str, int] = {}
    for name in names:
        counts[name] = await src[name].count_documents({})
        print(f"  {name}: {counts[name]} docs")
    total = sum(counts.values())
    print(f"  TOTAL: {total} docs")

    if not apply:
        print("\nDry-run: no se escribió nada. Ejecutá con --apply para migrar.")
        return 0

    from app.core.db.lakebase import LakebaseDatabase, create_pool
    from app.core.db.lakebase.translate import dumps_canonical
    from app.core.db.indexes import ensure_indexes

    pool = await create_pool()
    db = LakebaseDatabase(pool, settings.LAKEBASE_PGSCHEMA)
    await db.ensure_base()

    migrated = 0
    for name in names:
        await db.ensure_table(name)
        async with pool.acquire() as conn:
            await conn.execute(f'TRUNCATE "{db.schema}"."{name}"')
        cursor = src[name].find({})
        batch_ids: list[str] = []
        batch_docs: list[str] = []
        n = 0

        async def flush():
            nonlocal batch_ids, batch_docs
            if not batch_ids:
                return
            async with pool.acquire() as conn:
                await conn.execute(
                    f'INSERT INTO "{db.schema}"."{name}" (id, doc) '
                    f"SELECT u.uid, u.udoc::jsonb FROM unnest($1::text[], $2::text[]) AS u(uid, udoc) "
                    f"ON CONFLICT (id) DO UPDATE SET doc = EXCLUDED.doc",
                    batch_ids, batch_docs,
                )
            batch_ids, batch_docs = [], []

        async for raw in cursor:
            doc = canon(raw)
            _id = str(doc.get("_id"))
            doc["_id"] = _id
            batch_ids.append(_id)
            batch_docs.append(dumps_canonical(doc))
            n += 1
            if len(batch_ids) >= BATCH:
                await flush()
        await flush()
        migrated += n
        estado = "OK" if n == counts[name] else f"⚠ esperaba {counts[name]}"
        print(f"  → {name}: {n} docs migrados [{estado}]")

    print("Asegurando índices (btree/GIN) …")
    await ensure_indexes(db)
    await pool.close()
    mongo.close()
    print(f"\nLISTO: {migrated}/{total} docs migrados. "
          f"Siguiente paso: `python -m scripts.lakebase.verify_migration`.")
    return 0 if migrated == total else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="escribir en Lakebase (sin esto: dry-run de conteos)")
    args = ap.parse_args()
    load_dotenv()
    sys.exit(asyncio.run(run(args.apply)))


if __name__ == "__main__":
    main()
