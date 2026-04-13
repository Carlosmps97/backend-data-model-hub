"""Test v2 — ejecuta el pipeline y muestra resultado completo parseado."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.factory import get_chat_client
from src.tools.excel_tools import parse_excel_file
from src.workflow.graph import run_modeling_pipeline


def strip_md_fences(text: str) -> str:
    """Elimina fences de markdown (```json ... ```) del texto."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
    if t.endswith("```"):
        t = t[:-3]
    return t.strip()


async def test():
    print("=" * 60)
    print("TEST v2: Pipeline completo con sample_input.xlsx")
    print("=" * 60)

    # 1. Parsear Excel
    excel_result = parse_excel_file.func("data/sample_input.xlsx")
    excel_data = json.loads(excel_result)
    print(f"\n[Excel] {excel_data['total_tables']} tabla(s)")

    # 2. Ejecutar pipeline
    input_data = {
        "user_text": "Modela estas tablas para ecommerce. orders tiene FK hacia customers.",
        "tables": excel_data["tables"],
        "target_engine": "databricks_sql",
        "relationships": ["orders FK → customers por identificador de cliente"],
    }

    print("[Pipeline] Ejecutando...")
    client = get_chat_client()
    result = await run_modeling_pipeline(client, input_data)

    # 3. Parsear modelo generado
    gm = result.get("generated_model", "")
    gm_clean = strip_md_fences(gm)
    try:
        model = json.loads(gm_clean)
        print(f"\n{'='*60}")
        print("MODELO GENERADO")
        print(f"{'='*60}")
        print(f"Engine: {model.get('engine')}")
        print(f"Relaciones: {model.get('relationships', [])}")
        for t in model.get("tables", []):
            print(f"\n  TABLE: {t['table_name']} ({len(t['columns'])} columnas)")
            for c in t["columns"]:
                pk = " [PK]" if c.get("is_primary_key") else ""
                fk = f" [FK→{c.get('fk_reference')}]" if c.get("is_foreign_key") else ""
                null = "NULL" if c.get("is_nullable") else "NOT NULL"
                print(f"    {c['column_name']:30s} {c['data_type']:15s} {null:8s}{pk}{fk}")
            ddl = t.get("ddl", "")
            if ddl:
                print(f"\n  DDL:\n{ddl[:500]}")
    except Exception as e:
        print(f"[WARN] No se pudo parsear modelo: {e}")
        print(gm_clean[:500])

    # 4. Parsear QA
    qa = result.get("qa_validation", "")
    qa_clean = strip_md_fences(qa)
    try:
        qa_data = json.loads(qa_clean)
        report = qa_data.get("qa_report", {})
        print(f"\n{'='*60}")
        print("REPORTE QA")
        print(f"{'='*60}")
        print(f"Score: {report.get('quality_score')}/100")
        print(f"Summary: {report.get('summary', '')[:300]}")

        std = report.get("standardized_columns", [])
        if std:
            print(f"\nColumnas estandarizadas ({len(std)}):")
            for s in std:
                print(f"  {s.get('original_name','')} → {s.get('standardized_name','')}  ({s.get('reason','')[:80]})")

        viols = report.get("guideline_violations", [])
        if viols:
            print(f"\nViolaciones ({len(viols)}):")
            for v in viols:
                print(f"  [{v.get('table_name')}.{v.get('column_name')}] {v.get('violation','')[:80]}")

        new = report.get("new_catalog_entries", [])
        if new:
            print(f"\nNuevas entradas catálogo ({len(new)}):")
            for n in new:
                print(f"  {n.get('column_name')} ({n.get('data_type')}) — {n.get('functional_definition','')[:60]}")
    except Exception as e:
        print(f"[WARN] No se pudo parsear QA: {e}")
        print(qa_clean[:500])

    print(f"\n{'='*60}")
    print("TEST v2 COMPLETADO")


if __name__ == "__main__":
    asyncio.run(test())
