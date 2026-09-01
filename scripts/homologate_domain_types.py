"""Homologación de `defaultDataType` de parent domains (doc 62) sobre la BD.

Los dominios migrados de Erwin quedaron con la grafía CRUDA del XML
(`Array`, `BIG INTEGER`, `DECIMAL (22,4)` con espacio) y nada los comparaba
contra el catálogo canónico de la plataforma: al asociar el dominio, la
columna heredaba el string tal cual y el combobox de tipos no lo reconocía
(p.ej. `Array` no abre el builder de `ARRAY<>`).

Este script re-escribe cada dominio a su forma canónica
(`app.core.datatypes.canonicalize_default_type`) y CASCADEA como lo hace
`update_domain` (doc M1a): re-tipa las columnas publicadas que MANTIENEN el
tipo viejo del dominio (`dataType == viejo`) sin override manual. Además
re-escribe los payloads de columnas en changesets DRAFT (mismo criterio) —
sin eso, un draft vivo seguiría mostrando el tipo crudo. Los changesets
`submitted` NO se tocan (lo que el revisor ve es sagrado): solo se reportan.

Por defecto DRY-RUN (no escribe nada). Con `--apply` escribe y re-lee para
verificar.

Uso (desde la raíz del backend, con la BD alcanzable):
    .venv/bin/python scripts/homologate_domain_types.py            # dry-run
    .venv/bin/python scripts/homologate_domain_types.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.datatypes import canonical_type, canonicalize_default_type  # noqa: E402
from app.core.db.client import connect, disconnect, get_db  # noqa: E402

ACT = {"flgactive": {"$ne": False}}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_canonical(t: str) -> bool:
    """Canónico = tipo del catálogo o complejo 'recién iniciado' (`X<>`)."""
    return canonical_type(t) is not None or t in ("ARRAY<>", "MAP<>", "STRUCT<>")


async def main(apply: bool) -> None:
    await connect()
    db = await get_db()

    domains = await db["parent_domains"].find(ACT).to_list(None)
    domains.sort(key=lambda d: (d.get("name") or "").lower())

    drafts = [str(c["_id"]) for c in await db["changesets"].find(
        {"status": "draft"}, {"_id": 1}).to_list(None)]
    submitted = [str(c["_id"]) for c in await db["changesets"].find(
        {"status": "submitted"}, {"_id": 1}).to_list(None)]

    async def draft_hits(cs_ids: list[str], did: str, old: str) -> list[dict]:
        if not cs_ids:
            return []
        rows = await db["changeset_changes"].find(
            {"csId": {"$in": cs_ids}, "collection": "canonical_columns",
             "op": "upsert"}).to_list(None)
        return [r for r in rows
                if (p := r.get("payload") or {}).get("parentDomainId") == did
                and p.get("dataType") == old and not p.get("typeOverridden")]

    plan = []          # (doc, old, new, n_cols, draft_rows, n_submitted)
    foreign = []       # tipos que ni homologados son del catálogo (verbatim)
    for d in domains:
        old = d.get("defaultDataType") or ""
        new = canonicalize_default_type(old)
        if not _is_canonical(new):
            foreign.append((d.get("name"), old))
        if new == old:
            continue
        did = str(d["_id"])
        n_cols = len(await db["canonical_columns"].find(
            {**ACT, "parentDomainId": did, "typeOverridden": {"$ne": True},
             "dataType": old}, {"_id": 1}).to_list(None))
        d_rows = await draft_hits(drafts, did, old)
        n_sub = len(await draft_hits(submitted, did, old))
        plan.append((d, old, new, n_cols, d_rows, n_sub))

    print(f"dominios activos: {len(domains)} | a homologar: {len(plan)} | "
          f"fuera de catálogo (quedan verbatim): {len(foreign)}")
    for d, old, new, n_cols, d_rows, n_sub in plan:
        extra = f" · draft:{len(d_rows)}" if d_rows else ""
        extra += f" · SUBMITTED:{n_sub} (no se toca)" if n_sub else ""
        print(f"  {d.get('name')}: {old!r} -> {new!r} · columnas:{n_cols}{extra}")
    for name, old in foreign:
        print(f"  [verbatim] {name}: {old!r} no es del catálogo (revisar a mano)")

    if not apply:
        print("\nDRY-RUN: nada se escribió. Corré con --apply para ejecutar.")
        await disconnect()
        return

    now = _now()
    total_cols = 0
    total_draft = 0
    for d, old, new, _n, d_rows, _s in plan:
        did = str(d["_id"])
        await db["parent_domains"].update_one(
            {"_id": did}, {"$set": {"defaultDataType": new, "updatedAt": now}})
        # Cascada M1a: solo columnas que MANTIENEN el tipo viejo y sin override.
        res = await db["canonical_columns"].update_many(
            {**ACT, "parentDomainId": did, "typeOverridden": {"$ne": True},
             "dataType": old},
            {"$set": {"dataType": new, "updatedAt": now}})
        total_cols += getattr(res, "modified_count", 0) or 0
        # Drafts: payload COMPLETO re-escrito (jamás dot-paths con keys de
        # usuario — invariante doc 56; acá la key es fija pero se re-escribe
        # entero igual, más simple de verificar).
        for row in d_rows:
            payload = dict(row.get("payload") or {})
            payload["dataType"] = new
            await db["changeset_changes"].update_one(
                {"_id": row["_id"]}, {"$set": {"payload": payload}})
            total_draft += 1

    # Verificación: re-leer y confirmar que no queda ninguno des-homologado.
    left = [
        (x.get("name"), x.get("defaultDataType"))
        for x in await db["parent_domains"].find(ACT).to_list(None)
        if canonicalize_default_type(x.get("defaultDataType") or "")
        != (x.get("defaultDataType") or "")
    ]
    print(f"\nAPLICADO: {len(plan)} dominios · {total_cols} columnas publicadas · "
          f"{total_draft} cambios draft re-escritos")
    print("verificación: 0 dominios pendientes ✔" if not left
          else f"ATENCIÓN: quedaron pendientes: {left}")
    await disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="escribe (default: dry-run)")
    args = ap.parse_args()
    asyncio.run(main(args.apply))
