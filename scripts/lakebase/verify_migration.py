"""Verificación de CONCORDANCIA Cosmos ↔ Lakebase (doc 28 §7).

Para cada colección de la base Mongo compara contra su tabla en Lakebase:
1. Conteo de documentos.
2. Diferencia de conjuntos de `_id` (faltantes/sobrantes).
3. SHA-256 del JSON canónico de CADA doc (claves ordenadas, misma
   canonicalización BSON→JSON que la migración) → lista exacta de docs
   que difieren.

    .venv/bin/python -m scripts.lakebase.verify_migration

Solo LEE ambos lados. Exit 0 = concordancia total; exit 1 = discrepancias
(las imprime, hasta 10 por colección).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys

from dotenv import load_dotenv

from scripts.lakebase.migrate_cosmos_to_lakebase import canon


def _sha(doc: dict) -> str:
    payload = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def run() -> int:
    from motor.motor_asyncio import AsyncIOMotorClient

    from app.core.config import settings
    from app.core.db.lakebase import create_pool

    mongo = AsyncIOMotorClient(settings.COSMOS_CONNECTION_STRING, serverSelectionTimeoutMS=15_000)
    src = mongo[settings.COSMOS_DATABASE]
    pool = await create_pool()
    schema = settings.LAKEBASE_PGSCHEMA

    names = sorted(await src.list_collection_names())
    print(f"Verificando {len(names)} colecciones: Cosmos `{settings.COSMOS_DATABASE}` "
          f"↔ Lakebase `{settings.PGDATABASE}`.{schema}\n")

    problemas = 0
    for name in names:
        # Lado Cosmos: {_id: sha}
        c_hash: dict[str, str] = {}
        async for raw in src[name].find({}):
            doc = canon(raw)
            doc["_id"] = str(doc.get("_id"))
            c_hash[doc["_id"]] = _sha(doc)
        # Lado Lakebase
        p_hash: dict[str, str] = {}
        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT count(*) FROM pg_tables WHERE schemaname=$1 AND tablename=$2",
                schema, name,
            )
            if exists:
                rows = await conn.fetch(f'SELECT id, doc FROM "{schema}"."{name}"')
                for r in rows:
                    p_hash[r["id"]] = _sha(json.loads(r["doc"]))
        faltan = sorted(set(c_hash) - set(p_hash))
        sobran = sorted(set(p_hash) - set(c_hash))
        difieren = sorted(k for k in set(c_hash) & set(p_hash) if c_hash[k] != p_hash[k])
        ok = not faltan and not sobran and not difieren
        marca = "✓" if ok else "✗"
        print(f"{marca} {name}: cosmos={len(c_hash)} lakebase={len(p_hash)} "
              f"faltan={len(faltan)} sobran={len(sobran)} difieren={len(difieren)}")
        for etiqueta, lista in (("FALTA", faltan), ("SOBRA", sobran), ("DIFIERE", difieren)):
            for _id in lista[:10]:
                print(f"    {etiqueta}: {_id}")
            if len(lista) > 10:
                print(f"    … y {len(lista) - 10} más")
        if not ok:
            problemas += 1

    await pool.close()
    mongo.close()
    if problemas:
        print(f"\nRESULTADO: {problemas} colecciones con discrepancias.")
        return 1
    print("\nRESULTADO: concordancia TOTAL (todas las colecciones idénticas).")
    return 0


def main() -> None:
    load_dotenv()
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
