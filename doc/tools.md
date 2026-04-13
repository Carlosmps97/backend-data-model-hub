# Referencia de Tools

## Visión general

Las tools son funciones Python decoradas con `@tool` del Microsoft Agent Framework. Los agentes las invocan automáticamente durante su ejecución para acceder a datos, parsear archivos o gestionar el catálogo.

Todas las tools usan `approval_mode="never_require"` (ejecución sin aprobación manual).

---

## parse_excel_file

**Archivo**: `src/tools/excel_tools.py`
**Usada por**: ConversationalAgent

### Descripción
Parsea un archivo Excel (`.xlsx`) donde cada pestaña representa una tabla del modelo de datos. Extrae columnas con sus definiciones funcionales, tipos de dato sugeridos y metadatos.

### Parámetros
| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `file_path` | `str` | Ruta absoluta o relativa al archivo `.xlsx` |

### Retorno
JSON string con la estructura:
```json
{
  "tables": [
    {
      "table_name": "NombrePestaña",
      "columns": [
        {
          "column_name": "nombre",
          "functional_definition": "descripción funcional",
          "data_type_hint": "tipo sugerido",
          "is_nullable": true,
          "notes": "observaciones"
        }
      ]
    }
  ],
  "total_tables": 2
}
```

### Header matching flexible
La tool reconoce múltiples variaciones de nombres de columna en el Excel:

| Campo interno | Aliases reconocidos |
|---------------|---------------------|
| `column_name` | `columna`, `nombre`, `nombre_columna`, `col_name`, `field`, `campo` |
| `functional_definition` | `definicion`, `definicion_funcional`, `definition`, `descripcion`, `description`, `desc`, `funcional` |
| `data_type_hint` | `data_type`, `tipo`, `tipo_dato`, `type`, `type_hint`, `datatype` |
| `is_nullable` | `nullable`, `nulo`, `permite_nulos`, `null`, `nulable` |
| `notes` | `notas`, `observaciones`, `comentarios`, `comments`, `note` |

Si no se detecta `functional_definition`, la tool asume que la primera columna es el nombre y la segunda la definición.

### Valores booleanos para `is_nullable`
Acepta: `true`, `1`, `si`, `sí`, `yes`, `y`, `s` como verdadero.

### Errores
```json
{"error": "Archivo no encontrado: ruta/al/archivo.xlsx"}
{"error": "Formato no soportado: .csv. Solo .xlsx"}
{"error": "No se encontraron tablas válidas en el archivo Excel."}
```

---

## query_guidelines

**Archivo**: `src/tools/knowledge_base_tools.py`
**Usada por**: ExecutorAgent, QAValidatorAgent

### Descripción
Consulta los lineamientos corporativos de modelamiento de datos por tema específico.

### Parámetros
| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `topic` | `str` | Tema a consultar. Ejemplos: `naming_conventions`, `audit_columns`, `data_type_mappings`, `primary_key_conventions`, `foreign_key_conventions`, `general_rules`, `databricks_sql`, `sqlserver`, etc. |

### Lógica de búsqueda (formato JSON)
1. **Clave directa**: si `topic` coincide con una clave del JSON, retorna su valor.
2. **Motor de BD**: busca en `data_type_mappings[topic]` para mapeos por motor.
3. **Keywords**: busca coincidencias parciales en claves y valores del JSON.

### Ejemplo de uso por un agente
```
Agente llama: query_guidelines(topic="naming_conventions")
Retorna:
{
  "table_prefix": "tbl_",
  "column_case": "snake_case",
  "column_prefixes": {
    "identifier": "id_",
    "name": "name_",
    "date": "date_",
    ...
  },
  "max_column_name_length": 64
}
```

### Formatos de lineamientos soportados
- **JSON**: búsqueda por claves estructuradas.
- **XLSX**: cada pestaña como sección.
- **DOCX/PDF/TXT**: búsqueda por texto (grep por líneas que contengan el topic).

---

## get_all_guidelines

**Archivo**: `src/tools/knowledge_base_tools.py`
**Usada por**: ExecutorAgent

### Descripción
Retorna todos los lineamientos corporativos completos en formato JSON.

### Parámetros
Ninguno.

### Retorno
JSON string con el contenido completo del archivo de lineamientos.

---

## search_column_catalog

**Archivo**: `src/tools/catalog_tools.py`
**Usada por**: QAValidatorAgent

### Descripción
Busca en el catálogo corporativo columnas con definición funcional similar. Usa similitud por keywords (Jaccard index) para encontrar coincidencias.

### Parámetros
| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `functional_definition` | `str` | Definición funcional de la columna a buscar |

### Algoritmo de similitud
1. Tokeniza ambos textos en palabras.
2. Remueve stopwords (español e inglés).
3. Calcula **Jaccard similarity**: `|intersección| / |unión|`.
4. Umbral de inclusión: ≥ 0.3.
5. Umbral de match firme: ≥ 0.5.

### Retorno — con coincidencias
```json
{
  "found": true,
  "matches": [
    {
      "column_name": "date_created",
      "functional_definition": "Fecha y hora de creación del registro",
      "data_type": "TIMESTAMP",
      "used_in_tables": ["tbl_customers", "tbl_orders"],
      "similarity_score": 0.714
    }
  ],
  "best_match": { "..." },
  "recommendation": "USAR nombre estandarizado: 'date_created' (similitud: 0.714)"
}
```

### Retorno — sin coincidencias
```json
{
  "found": false,
  "matches": [],
  "recommendation": "No se encontró columna similar en el catálogo. Usar nombre propuesto y agregar al catálogo."
}
```

---

## add_column_to_catalog

**Archivo**: `src/tools/catalog_tools.py`
**Usada por**: QAValidatorAgent

### Descripción
Agrega una nueva columna al catálogo corporativo. Si la columna ya existe, agrega la tabla a su lista de `used_in_tables`. Las columnas nuevas se marcan con `is_new: true` para revisión posterior.

### Parámetros
| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `column_name` | `str` | Nombre de la columna |
| `functional_definition` | `str` | Definición funcional |
| `data_type` | `str` | Tipo de dato base |
| `table_name` | `str` | Tabla donde se usa |

### Retornos posibles
```json
// Columna nueva creada
{"status": "created", "message": "Columna 'price_total' agregada al catálogo como nueva."}

// Tabla agregada a columna existente
{"status": "updated", "message": "Tabla 'tbl_orders' agregada a columna existente 'id_customer'."}

// Columna ya existe con esa tabla
{"status": "exists", "message": "La columna 'id_customer' ya existe en el catálogo."}
```

### Thread safety
Usa `threading.Lock` para operaciones atómicas de lectura-escritura sobre `column_catalog.json`.

---

## get_full_column_catalog

**Archivo**: `src/tools/catalog_tools.py`
**Usada por**: QAValidatorAgent

### Descripción
Retorna el catálogo completo de columnas corporativas para validación integral.

### Parámetros
Ninguno.

### Retorno
JSON string con todo el contenido de `column_catalog.json`.

---

## Mapa de tools por agente

```
ConversationalAgent
  └── parse_excel_file

ExecutorAgent
  ├── query_guidelines
  └── get_all_guidelines

QAValidatorAgent
  ├── query_guidelines
  ├── search_column_catalog
  ├── add_column_to_catalog
  └── get_full_column_catalog
```
