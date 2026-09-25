"""Negocio de Data Standards: snapshot, apply (con versionado) y rollback.

Reutiliza las mutaciones existentes de `domains`/`dictionary`/`settings` (la
cascada de tipo y la re-derivación de nombres físicos ya viven ahí) y agrega la
capa de versionado: cada apply/rollback registra una `standards_versions` con
snapshot + diff + impacto + autor.

`build_diff` y `snapshot_of` son PUROS (testeables sin DB).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from app.core.audit import audit
from app.core.logging import get_logger
from app.features.ddl_rules import repository as rules_repo
from app.features.ddl_rules import service as rules_svc
from app.features.ddl_rules.engine import generators as ddl_generators
from app.features.ddl_rules.engine import validate as ddl_validate
from app.features.ddl_rules.output import normalize_output as ddl_normalize_output
from app.features.glossary import repository as dict_repo, service as dict_svc
from app.features.domains import repository as dom_repo, service as dom_svc
from app.features.settings import repository as set_repo, service as set_svc
from app.features.udp import repository as udp_repo

from . import repository
from .models import KINDS

log = get_logger("app.data_standards")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Puro ───────────────────────────────────────────────────────────────────


def snapshot_of(domains: list[dict], terms: list[dict], naming: dict,
                udp: list[dict] | None = None,
                ddl_rules: list[dict] | None = None,
                ddl_config: dict | None = None) -> dict:
    """Snapshot limpio del estado de estándares. Puro."""
    return {
        "domains": [
            {k: d.get(k) for k in ("id", "name", "defaultDataType", "namingTerm", "description",
                                   "logicalDataType", "inheritsName",             # doc 69/79
                                   "physicalName", "physicalDescription", "udpValues")}   # doc 85
            for d in domains
        ],
        "dict": [
            {k: t.get(k) for k in ("id", "term", "abbrev", "scope",
                                   "locked", "lockedBy", "lockedAt")}
            for t in terms
        ],
        "namingConfig": {
            scope: {"separator": (naming.get(scope) or {}).get("separator"),
                    "case": (naming.get(scope) or {}).get("case"),
                    "maxLength": (naming.get(scope) or {}).get("maxLength")}
            for scope in ("column", "table")
        },
        "udp": [
            {k: u.get(k) for k in ("id", "name", "level", "view", "dataType", "defaultValue",
                                   "allowedValues", "description")}   # doc 69: faceta
            for u in (udp or [])
        ],
        # DDL Export Rules (doc 30 D1): el doc entero — el rollback restaura la
        # regla tal cual, incluido su estado de validación de ese momento.
        "ddlRules": [
            {k: r.get(k) for k in ("id", "name", "description", "kind", "target",
                                   "sourceArtifact", "condition", "udpRefs", "action",
                                   "appliesTo", "priority", "enabled",
                                   "validationState", "validationReport", "updatedBy")}
            for r in (ddl_rules or [])
        ],
        "ddlConfig": {"lookups": (ddl_config or {}).get("lookups") or {},
                      "functions": (ddl_config or {}).get("functions") or [],
                      "output": (ddl_config or {}).get("output") or {}},
    }


def build_diff(body, before_domains: dict[str, dict], before_terms: dict[str, dict],
               before_udp: dict[str, dict] | None = None,
               before_rules: dict[str, dict] | None = None) -> dict:
    """Listas legibles de lo que el batch agrega/edita/quita (para el historial).
    `before_*` = mapas id→doc del estado PREVIO. Puro."""
    before_udp = before_udp or {}
    before_rules = before_rules or {}
    added: list[str] = []
    edited: list[str] = []
    removed: list[str] = []

    for u in getattr(body, "udpUpsert", []) or []:
        if u.id and u.id in before_udp:
            edited.append(f"UDP {u.name}")
        else:
            added.append(f"UDP {u.name}")
    for uid in getattr(body, "udpDelete", []) or []:
        prev = before_udp.get(uid)
        removed.append(f"UDP {prev['name']}" if prev else f"UDP {uid}")

    for r in getattr(body, "rulesUpsert", []) or []:
        label = "Generator" if r.kind == "generator" else "Rule"
        if r.id and r.id in before_rules:
            edited.append(f"{label} {r.name}")
        else:
            added.append(f"{label} {r.name}")
    for rid in getattr(body, "rulesDelete", []) or []:
        prev = before_rules.get(rid)
        kind = "Generator" if prev and prev.get("kind") == "generator" else "Rule"
        removed.append(f"{kind} {prev['name']}" if prev else f"Rule {rid}")
    patch = getattr(body, "ddlConfigPatch", None)
    if patch is not None:
        if patch.lookups is not None:
            edited.append(f"DDL lookups · {len(patch.lookups)} defined")
        if patch.functions is not None:
            edited.append(f"DDL functions · {len(patch.functions)} defined")
        if patch.output is not None:
            edited.append("DDL output settings")

    for t in body.termsUpsert:
        label = f"{t.term} → {t.abbrev}"
        if t.id and t.id in before_terms:
            edited.append(f"Term {label}")
        else:
            added.append(f"Term {label}")
    for tid in body.termsDelete:
        prev = before_terms.get(tid)
        removed.append(f"Term {prev['term']}" if prev else f"Term {tid}")

    for scope, rule in (body.namingConfig or {}).items():
        edited.append(f"{scope.capitalize()} naming · sep '{rule.separator}' · {rule.case} · max {rule.maxLength}")

    for d in body.domainsUpsert:
        if d.id and d.id in before_domains:
            prev = before_domains[d.id]
            parts = [f"Domain {d.name}"]
            if prev.get("defaultDataType") != d.defaultDataType:
                parts.append(f"{prev.get('defaultDataType')} → {d.defaultDataType}")
            # Doc 80 §4: prender/apagar la marca «atributo estándar» cambia qué se
            # estampa en CADA columna que adopte el dominio (nombre lógico +
            # definición funcional) — sale nombrado en el historial y el rollback,
            # no como un "Domain X" mudo. `None` = el cliente no mandó el flag.
            if d.inheritsName is not None and bool(prev.get("inheritsName")) != d.inheritsName:
                parts.append(f"inherits name + definition {'ON' if d.inheritsName else 'OFF'}")
            # Doc 85: el físico del dominio y sus UDP por defecto se heredan a
            # cada columna que lo adopte — salen nombrados en el historial.
            if d.physicalName is not None and (prev.get("physicalName") or "") != (d.physicalName or ""):
                parts.append(f"physical name {prev.get('physicalName') or '(derived)'} → {d.physicalName or '(derived)'}")
            if d.udpValues is not None and (prev.get("udpValues") or {}) != d.udpValues:
                parts.append("UDP defaults changed")
            edited.append(" · ".join(parts))
        else:
            added.append(f"Domain {d.name} · {d.defaultDataType}")
    for did in body.domainsDelete:
        prev = before_domains.get(did)
        removed.append(f"Domain {prev['name']}" if prev else f"Domain {did}")

    return {"added": added, "edited": edited, "removed": removed}


def _naming_key(snap: dict):
    """Parte del snapshot que determina los nombres físicos (glosario + naming).
    Dos snapshots con el mismo `_naming_key` producen los mismos physicalName →
    un rollback entre ellos no necesita re-physicalizar. Solo cuentan los campos
    que usa el motor (doc 94 D11): ni el lock ni metadata retirada como
    `wordType` de snapshots viejos disparan un re-derivado. Puro."""
    terms = sorted(({k: t.get(k) for k in ("id", "term", "abbrev", "scope")}
                    for t in (snap.get("dict") or [])), key=lambda t: t.get("id") or "")
    return (terms, snap.get("namingConfig") or {})


def _domains_key(snap: dict):
    """Dominios del snapshot (ordenados). Si no cambian entre target y actual, un
    rollback no necesita re-propagar tipos por dominio. Puro."""
    return sorted((snap.get("domains") or []), key=lambda d: d.get("id") or "")


# D4 protege el CONTENIDO de la entrada; los campos de lock (locked/lockedBy/
# lockedAt) NO cuentan en la comparación — el lock vigente nunca lo revierte un
# rollback (los snapshots pre-bloqueo traen locked=False: compararlos daba 409
# a TODO rollback con términos bloqueados, aunque el contenido fuera idéntico).
_TERM_CONTENT_KEYS = ("term", "abbrev", "scope")   # doc 94 D11: sin wordType


def remap_udp_values(values: dict | None, udp_map: dict[str, str]) -> dict[str, str] | None:
    """Doc 85 D11: `udpValues` de un dominio copiado — las claves son ids de
    defs UDP del proyecto FUENTE; se traducen con `udp_map` y las que no tienen
    def copiada se descartan. None si no queda nada. Puro."""
    if not values:
        return None
    out = {udp_map[k]: v for k, v in values.items() if k in udp_map}
    return out or None


def locked_terms_touched(snap_terms: list[dict], cur_terms: list[dict]) -> list[str]:
    """Términos actualmente BLOQUEADOS (D4) que restaurar `snap_terms` pisaría o
    eliminaría: no están en el snapshot (→ el restore los soft-deletea) o
    difieren en el CONTENIDO (→ los sobreescribe). Contenido idéntico = no-op →
    permitidos (esa entrada se preserva entera, ver `locked_ids_preserved`).
    Ambas listas en la forma de `snapshot_of`. Puro."""
    snap_by_id = {e.get("id"): e for e in snap_terms if e.get("id")}
    touched: list[str] = []
    for cur in cur_terms:
        if not cur.get("locked"):
            continue
        snap = snap_by_id.get(cur.get("id"))
        if snap is None or any(snap.get(k) != cur.get(k) for k in _TERM_CONTENT_KEYS):
            touched.append(cur.get("term"))
    return touched


def locked_ids_preserved(snap_terms: list[dict], cur_terms: list[dict]) -> set[str]:
    """Ids de entradas HOY bloqueadas con contenido idéntico en el snapshot: el
    restore las SALTA por completo (ni upsert ni soft-delete) — contenido y lock
    vigente sobreviven tal cual. Complemento de `locked_terms_touched` (una
    bloqueada o se toca → 409, o se preserva acá). Puro."""
    snap_by_id = {e.get("id"): e for e in snap_terms if e.get("id")}
    preserved: set[str] = set()
    for cur in cur_terms:
        if not cur.get("locked") or not cur.get("id"):
            continue
        snap = snap_by_id.get(cur["id"])
        if snap is not None and all(snap.get(k) == cur.get(k) for k in _TERM_CONTENT_KEYS):
            preserved.add(cur["id"])
    return preserved


def _title_for(body, diff: dict) -> str:
    if body.title:
        return body.title
    parts = diff["added"] + diff["edited"] + diff["removed"]
    if len(parts) == 1:
        return parts[0]
    return f"{len(parts)} standard changes" if parts else "Standard change"


# ── Async ──────────────────────────────────────────────────────────────────


async def current_snapshot(project_id: str) -> dict:
    """Estado actual de los estándares DEL PROYECTO (doc 75 D3)."""
    domains = await dom_repo.list_domains(project_id)
    terms = await dict_repo.list_entries(project_id, None)
    naming = await set_svc.get_naming(project_id)
    udp = await udp_repo.list_udp(project_id)
    ddl_rules = await rules_repo.list_rules(project_id)
    ddl_config = await rules_repo.get_config(project_id)
    return snapshot_of(domains, terms, naming, udp, ddl_rules, ddl_config)


async def history(project_id: str) -> list[dict]:
    return await repository.list_versions(project_id)


async def _record(actor: str, project_id: str, kind: str, title: str, description: str | None,
                  diff: dict, impact: dict, reverts_seq: int | None = None) -> dict:
    # El seq (max+1 DEL PROYECTO) y el reintento ante colisión concurrente los
    # resuelve el repositorio (`insert_version_next_seq`): el índice único
    # (project_id, seq) impide dos versiones con el mismo número. El snapshot
    # no depende del seq.
    snapshot = await current_snapshot(project_id)
    now = _now()
    return await repository.insert_version_next_seq(project_id, {
        "kind": kind, "title": title, "description": description, "author": actor,
        "createdAt": now, "appliedAt": now, "status": "applied",
        "diff": diff, "impact": impact, "snapshot": snapshot, "revertsSeq": reverts_seq,
    })


async def apply(actor: str, project_id: str, body) -> dict:
    """Aplica el batch a las colecciones publicadas DEL PROYECTO + re-deriva +
    registra la versión. Devuelve la versión creada."""
    before_domains = {d["id"]: d for d in await dom_repo.list_domains(project_id)}
    before_terms = {t["id"]: t for t in await dict_repo.list_entries(project_id, None)}
    before_udp = {u["id"]: u for u in await udp_repo.list_udp(project_id)}
    before_rules = {r["id"]: r for r in await rules_repo.list_rules(project_id)}

    # ── Guards de DDL Export Rules (doc 30) — fail-fast, sin estado a medias ──
    rules_upsert = getattr(body, "rulesUpsert", []) or []
    rules_delete = set(getattr(body, "rulesDelete", []) or [])
    ddl_patch = getattr(body, "ddlConfigPatch", None)
    # Doc 93 D1: las Output settings se validan ANTES de escribir nada (fail-fast).
    ddl_output = None
    if ddl_patch is not None and ddl_patch.output is not None:
        try:
            ddl_output = ddl_normalize_output(ddl_patch.output)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

    # Estado de reglas POST-batch: (previas − borradas) pisadas por los upserts.
    post_rules: dict[str, dict] = {rid: r for rid, r in before_rules.items()
                                   if rid not in rules_delete}
    for i, r in enumerate(rules_upsert):
        data = r.model_dump()
        # Key sintética por posición para las nuevas: dos altas con el mismo
        # name NO deben pisarse en el dict (el guard de unicidad las ve a ambas).
        rid = data.get("id") or f"__new__{i}"
        post_rules[rid] = data

    # Unicidad de `name` entre reglas activas (cubre duplicado intra-batch y
    # colisión con una existente): el slug identifica la regla en el catálogo,
    # el YAML y el log del export.
    names_seen: dict[str, str] = {}
    for rid, r in post_rules.items():
        n = (r.get("name") or "").strip()
        if not n:
            raise HTTPException(status_code=422, detail="Every rule needs a name.")
        if n in names_seen:
            raise HTTPException(
                status_code=409,
                detail=f"There is already a rule named '{n}'. Rule names must be unique.")
        names_seen[n] = rid

    # Borrar un UDP referenciado por reglas activas (post-batch) o por el origen
    # de un lookup se BLOQUEA con la lista (spec §4). Si el mismo batch borra la
    # regla que lo referenciaba, pasa.
    udp_delete_ids = set(getattr(body, "udpDelete", []) or [])
    ddl_config_now = await rules_repo.get_config(project_id)
    post_config = {
        "lookups": (ddl_patch.lookups if ddl_patch is not None and ddl_patch.lookups is not None
                    else ddl_config_now.get("lookups") or {}),
        "functions": (ddl_patch.functions if ddl_patch is not None and ddl_patch.functions is not None
                      else ddl_config_now.get("functions") or []),
    }
    if udp_delete_ids:
        refs = rules_svc.rules_referencing(list(post_rules.values()), udp_delete_ids)
        lk_refs = rules_svc.lookups_referencing(post_config["lookups"], udp_delete_ids)
        if refs or lk_refs:
            names = ", ".join(f"'{r['name']}'" for r in refs)
            extra = (f" · lookups: {', '.join(lk_refs)}" if lk_refs else "")
            raise HTTPException(
                status_code=409,
                detail=(f"That UDP is referenced by DDL export rules ({names}{extra}). "
                        "Delete or edit those rules first."))

    # Validación server-side AUTORITATIVA de las reglas del batch (spec §9:
    # guardar con errores está bloqueado). Se valida contra el estado POST-batch
    # de defs de UDP — un UDP creado en este mismo batch ya cuenta (para eso se
    # pre-asigna id a las altas). Editar solo props sueltas (toggle/priority) de
    # una regla YA inválida se permite: el export la salta igual.
    for u in getattr(body, "udpUpsert", []) or []:
        if not u.id:
            u.id = str(uuid.uuid4())
    post_udp: list[dict] = [u for uid, u in before_udp.items() if uid not in udp_delete_ids]
    for u in getattr(body, "udpUpsert", []) or []:
        d = u.model_dump()
        post_udp = [x for x in post_udp if x.get("id") != d["id"]]
        post_udp.append(d)
    post_artifacts = [a["id"] for a in rules_svc.artifact_catalog(list(post_rules.values()))]
    post_kinds = rules_svc.artifact_kinds(list(post_rules.values()))     # doc 71 H4
    _CORE_FIELDS = ("condition", "action", "appliesTo", "target", "kind", "sourceArtifact")
    rules_reports: list[tuple[dict, str]] = []   # (reporte, estado a persistir) por upsert
    for r in rules_upsert:
        data = r.model_dump()
        report = ddl_validate.validate_rule(data, post_udp, post_config, post_artifacts, post_kinds)
        prev = before_rules.get(r.id) if r.id else None
        core_edited = prev is None or any(data.get(f) != prev.get(f) for f in _CORE_FIELDS)
        if report["state"] == "invalid" and core_edited:
            first = report["errors"][0]
            raise HTTPException(
                status_code=400,
                detail=f"Rule '{data['name']}' has a validation error: {first['message']}")
        state = report["state"] if core_edited else ddl_validate.passive_state(report)
        rules_reports.append((report, state))

    # Cascada de generadores (spec §7.3) — solo si el batch toca reglas: borrar
    # un generador cuya salida es la FUENTE de otro activo → 409 con la lista;
    # un ciclo entre generadores del estado post-batch → 400 (se rechaza al
    # validar, nunca en runtime).
    if rules_upsert or rules_delete:
        deleted_emits = {(((before_rules.get(rid) or {}).get("action") or {}).get("emit") or {}).get("artifact")
                         for rid in rules_delete}
        deleted_emits.discard(None)
        if deleted_emits:
            dependents = sorted(r.get("name") or "" for r in post_rules.values()
                                if r.get("kind") == "generator"
                                and r.get("sourceArtifact") in deleted_emits)
            if dependents:
                raise HTTPException(
                    status_code=409,
                    detail=("That generator's output feeds other generators "
                            f"({', '.join(dependents)}). Delete or repoint those first."))
        cycle = ddl_generators.cycle_error(list(post_rules.values()))
        if cycle:
            raise HTTPException(status_code=400, detail=cycle)

    # F2 #1: guards del glosario ANTES de mutar nada (fail-fast, sin estado a
    # medias). Entradas bloqueadas (D4) → 409 para todos; validación contra
    # glosario + corpus de nombres lógicos (misma regla que el CRUD directo:
    # altas y renombres de texto validan (renombres con exclude_id); edits de
    # abbrev no. El botón Validar del front es cortesía).
    for tid in body.termsDelete:
        prev = before_terms.get(tid)
        if prev and prev.get("locked"):
            raise HTTPException(
                status_code=409,
                detail=(f"The term '{prev['term']}' is locked by an admin; "
                        "unlock it before deleting it."))
    claimed: set[tuple[str, str]] = set()  # (término normalizado, scope) que ESTE batch da de alta/renombra
    for t in body.termsUpsert:
        prev = before_terms.get(t.id) if t.id else None
        if prev and prev.get("locked"):
            raise HTTPException(
                status_code=409,
                detail=(f"The term '{prev['term']}' is locked by an admin; "
                        "unlock it before editing it."))
        renamed = prev is not None and (
            (t.term or "").strip().lower() != (prev.get("term") or "").strip().lower())
        if prev is None or renamed:
            # Duplicado INTRA-batch: ensure_term_valid valida contra el estado
            # PRE-batch, así que dos altas (o renombres) al mismo término+scope
            # dentro de UN apply pasaban las dos y se escribían ambas. Seen-set
            # batch-efectivo → colisión dentro del batch = mismo 409, sin mutar.
            key = ((t.term or "").strip().lower(), t.scope)
            if key in claimed:
                raise HTTPException(
                    status_code=409,
                    detail=(f"The term '{t.term}' appears more than once in this "
                            f"batch (scope '{t.scope}'); remove the duplicate before "
                            "applying."))
            claimed.add(key)
        if prev is None:  # término AÑADIDO (id nuevo o inexistente)
            await dict_svc.ensure_term_valid(project_id, t.term, t.scope)
        elif renamed:
            # renombre de TEXTO de un término existente → valida excluyéndose.
            await dict_svc.ensure_term_valid(project_id, t.term, t.scope, exclude_id=t.id)

    # Impacto de dominios (columnas re-tipadas): se cuenta ANTES de aplicar,
    # sobre las columnas sin override cuyo tipo cambia.
    domain_cols = 0
    for d in body.domainsUpsert:
        if d.id and d.id in before_domains and before_domains[d.id].get("defaultDataType") != d.defaultDataType:
            imp = await dom_svc.impact(d.id)
            domain_cols += imp.get("willUpdate", 0)

    # 1) Aplicar términos.
    for tid in body.termsDelete:
        await dict_repo.delete_entry(tid)
    for t in body.termsUpsert:
        data = t.model_dump(exclude_none=True)
        if t.id and t.id in before_terms:
            await dict_repo.update_entry(t.id, {k: v for k, v in data.items() if k != "id"})
        else:
            await dict_repo.create_entry(project_id, data)
    # 2) naming_config.
    for scope, rule in (body.namingConfig or {}).items():
        await set_repo.upsert(project_id, scope, rule.model_dump())
    # 3) Dominios (update cascada el tipo; create; delete).
    for did in body.domainsDelete:
        await dom_repo.delete_domain(did)
    for d in body.domainsUpsert:
        data = d.model_dump(exclude_none=True)
        if d.id and d.id in before_domains:
            await dom_repo.update_domain(d.id, {k: v for k, v in data.items() if k != "id"},
                                         cascade=True)
        else:
            await dom_repo.create_domain(project_id, data)

    # 4) Definiciones UDP (etiquetas key-value). NO cascadean nombres/tipos:
    #    solo definen las keys disponibles; los valores viven en las entidades.
    for uid in getattr(body, "udpDelete", []) or []:
        await udp_repo.delete_udp(uid)
    for u in getattr(body, "udpUpsert", []) or []:
        data = u.model_dump(exclude_none=True)
        if u.id and u.id in before_udp:
            await udp_repo.update_udp(u.id, {k: v for k, v in data.items() if k != "id"})
        else:
            await udp_repo.create_udp(project_id, data)

    # 4b) Reglas de DDL Export (doc 30). Tampoco cascadean nada del modelo: solo
    #     definen transformaciones del texto exportado. Se persiste el resultado
    #     de la validación autoritativa (estado, reporte, condición canonizada y
    #     udpRefs derivados server-side).
    for rid in rules_delete:
        await rules_repo.delete_rule(rid)
    for r, (report, state) in zip(rules_upsert, rules_reports):
        data = r.model_dump()
        data.update(
            updatedBy=actor,
            condition=report["condition"],
            udpRefs=report["udpRefs"],
            validationState=state,
            validationReport={k: report[k] for k in ("state", "checks", "errors", "warnings")},
        )
        if r.id and r.id in before_rules:
            await rules_repo.update_rule(r.id, {k: v for k, v in data.items() if k != "id"})
        else:
            await rules_repo.create_rule(project_id, data)
    # 4c) Config del ruleset (lookups/functions/output): cada bloque no-None reemplaza.
    if ddl_patch is not None and (ddl_patch.lookups is not None or ddl_patch.functions is not None
                                  or ddl_output is not None):
        await rules_repo.set_config(project_id, lookups=ddl_patch.lookups,
                                    functions=ddl_patch.functions, output=ddl_output)
    # 4d) Cambios de UDP re-validan las reglas NO tocadas del batch: un valor
    #     eliminado de una lista las marca 'stale' (spec §4); un rename se
    #     re-canoniza vía udpRefs. Colección chica (docenas) — barato.
    if (getattr(body, "udpUpsert", []) or []) or udp_delete_ids:
        touched = {r.id for r in rules_upsert if r.id} | rules_delete
        for rid, prev in before_rules.items():
            if rid in touched:
                continue
            rep = ddl_validate.validate_rule(prev, post_udp, post_config, post_artifacts, post_kinds)
            new_state = ddl_validate.passive_state(rep)
            if (new_state != prev.get("validationState")
                    or rep["condition"] != prev.get("condition")):
                await rules_repo.update_rule(rid, {
                    "condition": rep["condition"], "udpRefs": rep["udpRefs"],
                    "validationState": new_state,
                    "validationReport": {k: rep[k] for k in ("state", "checks", "errors", "warnings")},
                })

    # 5) Re-derivar nombres físicos si cambió el glosario o el naming_config.
    rederived = {"tables": 0, "columns": 0}
    if body.termsUpsert or body.termsDelete or body.namingConfig:
        # Doc 75 D3: SÓLO las tablas/columnas de ESTE proyecto.
        rederived = (await dict_svc.rephysicalize(project_id))["updated"]

    impact = {"tables": rederived["tables"], "columns": rederived["columns"] + domain_cols}
    diff = build_diff(body, before_domains, before_terms, before_udp, before_rules)
    # `kind` coaccionado al vocabulario conocido (el historial itera iconos por
    # kind; un valor arbitrario del cliente rompería el render).
    kind = body.kind if body.kind in KINDS else "batch"
    version = await _record(actor, project_id, kind, _title_for(body, diff), body.description, diff, impact)
    await audit(actor, "standards.apply", target=version["label"], target_type="standards_version",
                meta={"impact": impact, "projectId": project_id})
    log.info("standards applied", extra={"seq": version["seq"], "impact": impact})
    return version


async def rollback(actor: str, project_id: str, target_seq: int) -> dict | None:
    """Restaura el estado de estándares DEL PROYECTO al snapshot de `target_seq`,
    re-deriva, y registra una versión NUEVA (kind=rollback). None si la versión
    no existe. Doc 75 I5: todo restore va acotado al proyecto."""
    target = await repository.get_version(project_id, target_seq)
    if target is None:
        return None
    snap = target.get("snapshot") or {}

    # ¿El rollback cambia el glosario o el naming_config? Solo entonces hace falta
    # re-physicalizar (a 400k columnas eso son ~seg/decenas de seg). Un rollback
    # de solo-UDP o solo-dominios NO toca nombres físicos → se evita el barrido.
    cur = await current_snapshot(project_id)

    # D4: el restore deja `glossary_terms` EXACTAMENTE como el snapshot — sin
    # este guard pisaría/eliminaría entradas HOY bloqueadas. Solo cuenta el
    # CONTENIDO (los campos de lock no): con contenido idéntico el restore es
    # no-op y la entrada se PRESERVA entera (el lock vigente nunca se revierte).
    # Bloqueado + contenido tocado = intocable hasta desbloquear primero (unlock
    # admin-only y auditado): mismo 409 que CRUD/apply, con la lista.
    locked = locked_terms_touched(snap.get("dict") or [], cur.get("dict") or [])
    if locked:
        names = ", ".join(f"'{t}'" for t in locked)
        raise HTTPException(
            status_code=409,
            detail=(f"The rollback would overwrite or delete terms locked by an "
                    f"admin ({names}); unlock them before restoring."))
    preserve = locked_ids_preserved(snap.get("dict") or [], cur.get("dict") or [])

    naming_changed = (_naming_key(snap) != _naming_key(cur))
    domains_changed = (_domains_key(snap) != _domains_key(cur))

    await repository.restore_domains(project_id, snap.get("domains") or [])
    await repository.restore_dict(project_id, snap.get("dict") or [], preserve_ids=preserve)
    await repository.restore_naming(project_id, snap.get("namingConfig") or {})
    # Definiciones UDP: restaura las del snapshot (snapshots viejos sin 'udp' → []).
    await udp_repo.restore_udp(project_id, snap.get("udp") or [])
    # Reglas de DDL Export + config del ruleset (doc 30): mismo criterio —
    # snapshots pre-feature sin 'ddlRules'/'ddlConfig' dejan el catálogo vacío.
    await rules_repo.restore_rules(project_id, snap.get("ddlRules") or [])
    await rules_repo.restore_config(project_id, snap.get("ddlConfig") or {})

    # Re-derivar SOLO lo que cambió: nombres físicos si cambió glosario/naming;
    # tipos por dominio si cambiaron los dominios. Un rollback de solo-UDP no toca
    # ninguno de los dos → no barre las 400k columnas.
    rederived = (await dict_svc.rephysicalize(project_id))["updated"] if naming_changed else {"tables": 0, "columns": 0}
    domain_cols = 0
    if domains_changed:
        for d in (snap.get("domains") or []):
            if d.get("id"):
                domain_cols += (await dom_svc.propagate(d["id"])).get("updated", 0)

    impact = {"tables": rederived["tables"], "columns": rederived["columns"] + domain_cols}
    title = f"Rolled back to {target.get('label')}"
    diff = {"added": [], "edited": [f"Reverted standards to {target.get('label')}"], "removed": []}
    version = await _record(actor, project_id, "rollback", title, None, diff, impact, reverts_seq=target_seq)
    await audit(actor, "standards.rollback", target=target.get("label"),
                target_type="standards_version",
                meta={"newVersion": version["label"], "impact": impact, "projectId": project_id})
    log.info("standards rolled back", extra={"to": target_seq, "new": version["seq"]})
    return version


# ── Doc 75 D15: nacimiento de un proyecto (copia de bloques o baseline vacío) ──

COPY_BLOCKS = ("glossary", "domains", "udp", "naming", "ddl")
_UDP_KEYS = ("name", "level", "view", "dataType", "defaultValue", "allowedValues", "description")
_DOMAIN_KEYS = ("name", "defaultDataType", "logicalDataType", "namingTerm", "description", "inheritsName",
                "physicalName", "physicalDescription")   # doc 85 (`udpValues` se remapea aparte)
_TERM_KEYS = ("term", "abbrev", "scope")
_RULE_KEYS = ("name", "description", "kind", "target", "sourceArtifact", "condition", "action",
              "appliesTo", "priority", "enabled")


async def summary(project_id: str) -> dict:
    """Conteos por bloque para el asistente «New project» (doc 75 §8.3)."""
    return {
        "glossary": len(await dict_repo.list_entries(project_id, None)),
        "domains": len(await dom_repo.list_domains(project_id)),
        "udp": len(await udp_repo.list_udp(project_id)),
        "rules": len(await rules_repo.list_rules(project_id)),
        "naming": True,
    }


async def copy_standards(actor: str, target_project_id: str, source_project_id: str,
                         blocks: list[str], source_name: str | None = None) -> dict:
    """Copia UNA VEZ (foto, sin vínculo) los bloques pedidos del proyecto fuente al
    destino, con ids nuevos y las referencias a UDP remapeadas. Sin el bloque
    `udp`, las reglas que referencian UDP nacen `stale` y los lookups sin
    `fromUdpId`. Se registra como la versión seq 1 del destino (`kind=copy`)."""
    wanted = [b for b in blocks if b in COPY_BLOCKS]
    if len(wanted) != len(blocks) or not wanted:
        raise HTTPException(status_code=422,
                            detail=f"Unknown standards block; use {', '.join(COPY_BLOCKS)}.")
    label = source_name or source_project_id
    udp_map: dict[str, str] = {}
    if "udp" in wanted:
        for u in await udp_repo.list_udp(source_project_id):
            created = await udp_repo.create_udp(target_project_id, {k: u.get(k) for k in _UDP_KEYS})
            udp_map[u["id"]] = created["id"]
    if "domains" in wanted:
        for d in await dom_repo.list_domains(source_project_id):
            await dom_repo.create_domain(target_project_id, {
                **{k: d.get(k) for k in _DOMAIN_KEYS},
                # Doc 85 D11: sin el bloque `udp` no hay defs destino ⇒ sin defaults.
                "udpValues": remap_udp_values(d.get("udpValues"), udp_map) if "udp" in wanted else None,
            })
    if "glossary" in wanted:
        for t in await dict_repo.list_entries(source_project_id, None):
            await dict_repo.create_entry(target_project_id, {**{k: t.get(k) for k in _TERM_KEYS},
                                                              "locked": False, "lockedBy": None,
                                                              "lockedAt": None})
    if "naming" in wanted:
        for scope, rule in (await set_svc.get_naming(source_project_id)).items():
            await set_repo.upsert(target_project_id, scope,
                                  {k: rule.get(k) for k in ("separator", "case", "maxLength")})
    if "ddl" in wanted:
        for r in await rules_repo.list_rules(source_project_id):
            refs = [{"udpId": udp_map[x["udpId"]], "level": x["level"]}
                    for x in (r.get("udpRefs") or []) if x.get("udpId") in udp_map]
            lost = len(r.get("udpRefs") or []) - len(refs)
            await rules_repo.create_rule(target_project_id, {
                **{k: r.get(k) for k in _RULE_KEYS}, "udpRefs": refs, "updatedBy": actor,
                "validationState": "stale" if lost else r.get("validationState", "valid"),
                "validationReport": {} if lost else (r.get("validationReport") or {})})
        cfg = await rules_repo.get_config(source_project_id)
        lookups = {name: {**lk, "fromUdpId": udp_map.get((lk or {}).get("fromUdpId"))}
                   for name, lk in (cfg.get("lookups") or {}).items()}
        await rules_repo.set_config(target_project_id, lookups=lookups,
                                    functions=cfg.get("functions") or [], output=cfg.get("output") or {})
    diff = {"added": [f"{b} copied from «{label}»" for b in wanted], "edited": [], "removed": []}
    version = await _record(actor, target_project_id, "copy",
                            f"Copied from «{label}»: {', '.join(wanted)}", None, diff,
                            {"tables": 0, "columns": 0})
    await audit(actor, "standards.copy", target=version["label"], target_type="standards_version",
                meta={"projectId": target_project_id, "from": source_project_id, "blocks": wanted})
    return version


async def bootstrap_project(actor: str, project_id: str, copy_from: dict | None) -> dict:
    """Versión seq 1 del proyecto nuevo: copia (D15) o baseline vacío."""
    if copy_from:
        return await copy_standards(actor, project_id, copy_from["projectId"],
                                    list(copy_from["blocks"]), copy_from.get("projectName"))
    return await _record(actor, project_id, "baseline", "Initial standards", "Empty standards.",
                         {"added": [], "edited": [], "removed": []}, {"tables": 0, "columns": 0})
