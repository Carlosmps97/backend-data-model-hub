"""Test script — ejecuta el pipeline completo con el sample_input.xlsx."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.factory import get_chat_client
from src.tools.excel_tools import parse_excel_file
from src.workflow.graph import run_modeling_pipeline


async def test():
    print("=" * 60)
    print("TEST: Pipeline completo con sample_input.xlsx")
    print("=" * 60)

    # 1. Parsear Excel
    print("\n[1/3] Parseando Excel...")
    excel_result = parse_excel_file.func("data/sample_input.xlsx")
    excel_data = json.loads(excel_result)
    print(f"  ✓ {excel_data['total_tables']} tabla(s) encontrada(s)")

    # 2. Preparar input
    input_data = {
        "user_text": (
            "Modela estas tablas para un sistema de ecommerce. "
            "La tabla orders tiene FK hacia customers por el campo de identificador de cliente."
        ),
        "tables": excel_data["tables"],
        "target_engine": "databricks_sql",
        "relationships": [
            "La tabla orders tiene una foreign key hacia customers por el identificador del cliente"
        ],
    }

    # 3. Ejecutar pipeline
    print("\n[2/3] Ejecutando pipeline (ExecutorAgent → QAValidatorAgent)...")
    client = get_chat_client()
    result = await run_modeling_pipeline(client, input_data)

    # 4. Mostrar resultado
    print("\n[3/3] Resultado:")
    print(json.dumps(result, indent=2, ensure_ascii=False)[:5000])
    print("\n" + "=" * 60)
    print("TEST COMPLETADO")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test())
