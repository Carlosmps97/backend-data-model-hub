#!/usr/bin/env python3
"""Test: Generar DDL de tabla de telemetría de agentes para Databricks.

Usa las definiciones funcionales de la imagen proporcionada por el usuario.
Ejecutar con: .venv/bin/python scripts/test_telemetry_table.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.syntax import Syntax

from src.agents.factory import get_chat_client
from src.workflow.graph import run_modeling_pipeline

console = Console()


async def main():
    console.print(Panel(
        "[bold]Test: Tabla de telemetría de agentes para Databricks[/bold]\n"
        "Sin partición · Motor: databricks_sql",
        title="🧪 Test Pipeline",
        border_style="cyan",
    ))

    # Definiciones funcionales de la imagen
    input_data = {
        "user_text": (
            "Crea el DDL de una sola tabla para Databricks SIN partición. "
            "La tabla almacena telemetría de interacciones con agentes de IA. "
            "Usa los lineamientos corporativos de Databricks para naming, prefijos y tipos de dato. "
            "No incluyas columnas de partición."
        ),
        "tables": [
            {
                "table_name": "agent_telemetry",
                "columns": [
                    {
                        "column_name": "trace_id",
                        "functional_definition": "Identificador principal de la transacción.",
                        "data_type_hint": None,
                        "is_nullable": False,
                        "notes": None,
                    },
                    {
                        "column_name": "conversation_id",
                        "functional_definition": "Hilo lógico que agrupa trazas.",
                        "data_type_hint": None,
                        "is_nullable": True,
                        "notes": None,
                    },
                    {
                        "column_name": "agent_id",
                        "functional_definition": "Codigo indentificador unico de un agente la desplegado",
                        "data_type_hint": None,
                        "is_nullable": False,
                        "notes": None,
                    },
                    {
                        "column_name": "identity_sk",
                        "functional_definition": "Codigo unico autogenerado a partir de grupo de datos de identificacion.",
                        "data_type_hint": None,
                        "is_nullable": True,
                        "notes": None,
                    },
                    {
                        "column_name": "start_timestamp",
                        "functional_definition": "Fecha y hora inicio de la interaccion del agente con el usuario",
                        "data_type_hint": None,
                        "is_nullable": False,
                        "notes": None,
                    },
                    {
                        "column_name": "duration_ms",
                        "functional_definition": "Tiempo total en milisegundos que tardo en completarse la interaccion",
                        "data_type_hint": None,
                        "is_nullable": True,
                        "notes": None,
                    },
                    {
                        "column_name": "total_spans",
                        "functional_definition": "Cantidad total de spans generados en la interaccion",
                        "data_type_hint": None,
                        "is_nullable": True,
                        "notes": None,
                    },
                    {
                        "column_name": "tokens_in",
                        "functional_definition": "Volumen de tokens procesados.",
                        "data_type_hint": None,
                        "is_nullable": True,
                        "notes": None,
                    },
                    {
                        "column_name": "tokens_out",
                        "functional_definition": "Cantidad de tokens de salida",
                        "data_type_hint": None,
                        "is_nullable": True,
                        "notes": None,
                    },
                    {
                        "column_name": "tokens_total",
                        "functional_definition": "Cantidad de total de tokens generado in + out",
                        "data_type_hint": None,
                        "is_nullable": True,
                        "notes": None,
                    },
                    {
                        "column_name": "price_input",
                        "functional_definition": "El precio referencial de tokens input por 1000 para un determinado modelo",
                        "data_type_hint": None,
                        "is_nullable": True,
                        "notes": None,
                    },
                    {
                        "column_name": "price_output",
                        "functional_definition": "El precio referencial de tokens output por 1000 para un determinado modelo",
                        "data_type_hint": None,
                        "is_nullable": True,
                        "notes": None,
                    },
                    {
                        "column_name": "currency",
                        "functional_definition": "El tipo de moneda que representa el precio referencial de los tokens (usd/pen/yen/<etc>)",
                        "data_type_hint": None,
                        "is_nullable": True,
                        "notes": None,
                    },
                    {
                        "column_name": "cost_input",
                        "functional_definition": "Costo estimado de los tokens de entrada.",
                        "data_type_hint": None,
                        "is_nullable": True,
                        "notes": None,
                    },
                    {
                        "column_name": "cost_output",
                        "functional_definition": "Costo estimado de los tokens de salida.",
                        "data_type_hint": None,
                        "is_nullable": True,
                        "notes": None,
                    },
                    {
                        "column_name": "cost_total",
                        "functional_definition": "Sumatoria total financiera de la traza por el consumo de tokens.",
                        "data_type_hint": None,
                        "is_nullable": True,
                        "notes": None,
                    },
                    {
                        "column_name": "solution",
                        "functional_definition": "Nombre de la solucion asociado a la informacion procesada",
                        "data_type_hint": None,
                        "is_nullable": True,
                        "notes": None,
                    },
                    {
                        "column_name": "codapp",
                        "functional_definition": "Codigo de aplicativo de 4 digitos asociado a la informacion procesada.",
                        "data_type_hint": None,
                        "is_nullable": True,
                        "notes": None,
                    },
                    {
                        "column_name": "date_routine",
                        "functional_definition": "Fecha en formato yyyy-MM-dd en el cual ha sido procesado la informacion.",
                        "data_type_hint": None,
                        "is_nullable": True,
                        "notes": None,
                    },
                ],
            }
        ],
        "target_engine": "databricks_sql",
        "relationships": [],
    }

    # Mostrar input
    console.print("\n[bold yellow]📋 Input: Tabla de telemetría[/bold yellow]")
    tbl = Table(title="Columnas de entrada", show_lines=True)
    tbl.add_column("Campo", style="cyan")
    tbl.add_column("Definición Funcional", style="white")
    for col in input_data["tables"][0]["columns"]:
        tbl.add_row(col["column_name"], col["functional_definition"])
    console.print(tbl)

    # Conectar y ejecutar
    console.print("\n[bold green]🔗 Conectando con Azure AI Foundry...[/bold green]")
    try:
        client = get_chat_client()
        console.print("[green]✓ Conexión establecida[/green]\n")
    except Exception as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        return

    with console.status("[bold green]🔄 Ejecutando pipeline (ExecutorAgent → QAValidator)...[/bold green]", spinner="dots"):
        result = await run_modeling_pipeline(client, input_data)

    # Mostrar resultados
    if "error" in result:
        console.print(f"[red]✗ Error: {result['error']}[/red]")
        return

    console.print(Panel("[bold green]✓ Pipeline completado exitosamente[/bold green]", border_style="green"))

    # Modelo generado
    try:
        model = json.loads(result.get("generated_model", "{}"))
        console.print("\n[bold cyan]📊 Modelo Generado[/bold cyan]")

        for table in model.get("tables", []):
            console.print(f"\n[bold]Tabla: {table.get('table_name', 'N/A')}[/bold]")

            col_table = Table(show_lines=True)
            col_table.add_column("Columna", style="cyan")
            col_table.add_column("Tipo", style="green")
            col_table.add_column("Nullable", style="yellow")
            col_table.add_column("PK", style="red")
            col_table.add_column("Definición", style="white")

            for col in table.get("columns", []):
                if isinstance(col, dict):
                    col_table.add_row(
                        col.get("column_name", ""),
                        col.get("data_type", ""),
                        "✓" if col.get("is_nullable", True) else "✗",
                        "PK" if col.get("is_primary_key", False) else "",
                        col.get("functional_definition", "")[:50],
                    )
            console.print(col_table)

            ddl = table.get("ddl", "")
            if ddl:
                console.print(Panel(
                    Syntax(ddl, "sql", theme="monokai"),
                    title=f"DDL — {table.get('table_name', '')}",
                    border_style="blue",
                ))
    except json.JSONDecodeError:
        console.print("[yellow]Raw modelo:[/yellow]")
        console.print(result.get("generated_model", "N/A"))

    # QA Report
    try:
        qa = json.loads(result.get("qa_validation", "{}"))
        qa_report = qa.get("qa_report", {})

        console.print(f"\n[bold magenta]🏆 Quality Score: {qa_report.get('quality_score', 'N/A')}/100[/bold magenta]")

        std = qa_report.get("standardized_columns", [])
        if std:
            console.print("\n[bold]Columnas estandarizadas:[/bold]")
            for s in std:
                console.print(f"  {s.get('original_name', '')} → [green]{s.get('standardized_name', '')}[/green] ({s.get('reason', '')})")

        new_entries = qa_report.get("new_catalog_entries", [])
        if new_entries:
            console.print(f"\n[bold]Nuevas columnas en catálogo: {len(new_entries)}[/bold]")
            for e in new_entries:
                console.print(f"  • [cyan]{e.get('column_name', '')}[/cyan] ({e.get('data_type', '')})")

    except json.JSONDecodeError:
        console.print("[yellow]Raw QA:[/yellow]")
        console.print(result.get("qa_validation", "N/A"))


if __name__ == "__main__":
    asyncio.run(main())
