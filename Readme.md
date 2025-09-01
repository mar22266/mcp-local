# MCP Postgres Profiler

## Descripción

Servidor MCP local en **Python** para perfilar bases de datos **PostgreSQL**.

Expone herramientas vía **JSON-RPC** para realizar análisis y diagnósticos de consultas.

## Herramientas Disponibles

- **connect** → Conexión a la base de datos PostgreSQL.
- **explain** → Ejecución de `EXPLAIN` / `EXPLAIN ANALYZE` para planes de consulta.
- **slow_queries** → Detección de consultas lentas.
- **n_plus_one_suspicions** → Identificación de patrones sospechosos de N+1 queries.
- **index_suggestions** → Recomendaciones de índices basadas en consultas frecuentes.

---

# Pasos del HOST

Usaremos **3 terminales** (A: DB, B: servidor MCP, C: túnel).  
Mantén abiertas **B y C** mientras alguien te consume.

---

## 0) Clonar y crear entorno (una vez) — Terminal B

### Windows PowerShell

```powershell
git clone <URL_DE_ESTE_REPO> mcp-local
cd mcp-local
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configuración y Ejecución

### Crea `.env` a partir de `.env.example`

```env
# .env
```

## 1) Levantar PostgreSQL (Docker) — Terminal A

```bash
docker compose up -d
```

Por defecto: usuario mcp, password mcp123, DB mcpdb en localhost:5432

## 2) Iniciar el MCP por HTTP — Terminal B

Activa tu entorno virtual si no lo está:

```
. .\.venv\Scripts\Activate.ps1   # Windows
python server.py --http --host 127.0.0.1 --port 8765
```

Esto abre el transporte HTTP del MCP en: http://127.0.0.1:8765

## 3) Exponer MCP con un túnel público — Terminal C

en caos de no tenerlo: winget install Cloudflare.cloudflared

```bash
cloudflared tunnel --url http://127.0.0.1:8765
```

Te dará una URL pública tipo: https://<algo>.trycloudflare.com

### Comparte esta URL con el INTEGRATOR (su chatbot la usará como endpoint MCP).

### El INTEGRATOR no necesita acceso a tu Postgres ni correr Docker; solo consumirá tu MCP por HTTP.

## MCP Local — Endpoints y Contrato JSON-RPC

Este servidor expone JSON-RPC 2.0 sobre HTTP en un único endpoint.

### Endpoint y Transporte

- Endpoint HTTP: POST /
- Contenido: application/json
- Base URL local (por defecto): http://127.0.0.1:8787/
- Base URL pública (con túnel): https://<tu-subdominio>.<tunnel>

## Métodos JSON-RPC soportados

### 1) initialize (handshake opcional)

- params: { "protocolVersion": "YYYY-MM-DD", "capabilities": {} }
- result: { "capabilities": {} }

### 2) notifications/initialized (handshake opcional)

- params: {}
- result: {}

### 3) tools/list (alias aceptado: tools.list)

- params: {}
- result:

```bash
{
  "tools": [
    { "name": "connect",
      "inputSchema": {
        "type": "object",
        "properties": {
          "dsn": { "type": "string", "description": "postgresql://user:pass@host:5432/db" }
        },
        "required": ["dsn"],
        "additionalProperties": false
      }
    },
    { "name": "explain",
      "inputSchema": {
        "type": "object",
        "properties": {
          "sql":     { "type": "string" },
          "analyze": { "type": "boolean", "default": true },
          "buffers": { "type": "boolean", "default": true },
          "timing":  { "type": "boolean", "default": true }
        },
        "required": ["sql"],
        "additionalProperties": false
      }
    },
    { "name": "slow_queries",
      "inputSchema": {
        "type": "object",
        "properties": {
          "top": { "type": "integer", "default": 20, "minimum": 1, "maximum": 1000 }
        },
        "required": [],
        "additionalProperties": false
      }
    },
    { "name": "n_plus_one_suspicions",
      "inputSchema": {
        "type": "object",
        "properties": {
          "min_calls":   { "type": "integer", "default": 20,  "minimum": 1 },
          "max_avg_rows":{ "type": "number",  "default": 3.0, "minimum": 0 },
          "min_mean_ms": { "type": "number",  "default": 0.5, "minimum": 0 }
        },
        "required": [],
        "additionalProperties": false
      }
    },
    { "name": "index_suggestions",
      "inputSchema": {
        "type": "object",
        "properties": {
          "table": { "type": "string", "description": "schema.tabla, ej: public.orders" },
          "sample_sql": { "type": "string" },
          "validate_with_hypopg": { "type": "boolean", "default": true }
        },
        "required": ["table","sample_sql"],
        "additionalProperties": false
      }
    }
  ]
}
```

### 4) tools/call (alias aceptado: tools.call)

- params: { "name": "<tool_name>", "arguments": { ... } }
- result: estructura específica de cada herramienta (ver abajo).

## Herramientas (esquemas y ejemplos de payload)

Los chatbots anfitriones deben:

- llamar tools/list para leer inputSchema
- luego llamar tools/call con name y arguments.

## A) connect

- arguments:

```BASH
{ "dsn": "postgresql://mcp:mcp123@localhost:5432/mcpdb" }
```

- result (ejemplo):

```BASH
{
  "connected": true,
  "dsn": "postgresql://mcp:mcp123@localhost:5432/mcpdb",
  "meta": {
    "server_version": "...",
    "extensions_installed": ["plpgsql", "pg_stat_statements"],
    "extensions_available": ["pg_stat_statements", "hypopg", "..."],
    "pg_stat_statements": true,
    "hypopg": false
  }
}
```

## B) explain

- arguments:

```BASH
{
  "sql": "SELECT ...",
  "analyze": true,
  "buffers": true,
  "timing": true
}
```

- result (resumen):

```BASH
{
  "plan": [ { "Plan": { ... }, "Execution Time": 0.88 } ],
  "summary": {
    "dominant_node": { "node_type": "Limit", "metric": 0.854, "relation": null, "index_name": null },
    "plan_width": 28,
    "startup_cost": null,
    "total_cost": null,
    "actual_rows": 5,
    "actual_total_time_ms": 0.854
  }
}
```

## C) slow_queries

- arguments:

```BASH
{ "top": 10 }
```

- result:

```BASH
{
  "pg_stat_statements": true,
  "stats_reset": "2025-08-31T15:37:21.055545+00:00",
  "top": [
    { "queryid":"...", "calls":1, "rows":16000, "total_ms":67.97, "mean_ms":67.97, "normalized":"insert into ..." }
  ]
}
```

## D) n_plus_one_suspicions

- arguments:

```BASH
{ "min_calls": 500, "max_avg_rows": 2, "min_mean_ms": 0.002 }
```

- result:

```BASH
{
  "pg_stat_statements": true,
  "suspicions": [
    { "normalized":"select $? from only \"public\".\"users\" ...", "calls":120000, "avg_rows":1, "mean_ms":0.002005 }
  ]
}
```

## E) index_suggestions

- arguments:

```BASH
{
  "table": "public.users",
  "sample_sql": "SELECT id, email FROM users WHERE country='GT' ORDER BY email LIMIT 100;",
  "validate_with_hypopg": true
}
```

- result:

```BASH
{
  "explanation": [
    {
      "eq_cols": ["country"],
      "range_cols": [],
      "order_cols": [{ "column": "email", "direction": "asc" }],
      "target_alias": "users"
    }
  ],
  "suggestions": [
    {
      "table": "public.users",
      "columns_ordered": ["country", "email"],
      "create_index_sql": "CREATE INDEX ON public.users (country, email)"
    }
  ]
}
```

# Cómo ejecutarlo local y exponerlo

## 1. Inicia el servidor en modo JSON-RPC

```BASH
python server.py --jsonrpc --rpc-host 127.0.0.1 --rpc-port 8787
```

## 2. Expón una URL pública

cloudflared:

- cloudflared tunnel --url http://localhost:8787
- Usa la URL mostrada.

## 3. Entrega al integrador:

- Base URL pública (ej. https://abc123.BLABLA.io/)
- Esta sección de endpoints (métodos y schemas)

## Cómo lo usa un chatbot remoto (resumen)

- POST / con {"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}
- Leer inputSchema, construir formularios/parsers.
- POST / con {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"connect","arguments":{"dsn":"..."}}}
- Llamar explain, slow_queries, etc., según el flujo de su chatbot.

## Algunos ejemplos para pruebas del funcionamiento del Postgres Profiles

## Tabla de Pruebas

## Instalar paquete de hypopg

```bash
docker exec -it mcp-postgres bash
apt-get update
apt-get install -y postgresql-16-hypopg
```

Si sigues los pasos anteriores, estos son algunos ejemplos de pruebas que puedes ejecutar en el **Inspector**:

| **Tool**              | **Ejemplo** | **Qué pegar en los campos del Inspector**                                                                                                        | **Toggles**                                | **(Opcional) comandos en terminal**                                                                                                                                                                                                                                                                                                                                         | **Qué deberías ver**                                                                                  |
| --------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| **CONNECT**           | 1           | `dsn: postgresql://mcp:mcp123@localhost:5432/mcpdb`                                                                                              | —                                          | —                                                                                                                                                                                                                                                                                                                                                                           | `connected: true`, versión, extensiones                                                               |
| **EXPLAIN**           | 1           | `sql: SELECT * FROM USERS WHERE COUNTRY='GT' ORDER BY EMAIL LIMIT 5;`                                                                            | ANALYZE ON, BUFFERS ON, TIMING ON          | —                                                                                                                                                                                                                                                                                                                                                                           | Plan JSON con nodo **LIMIT/SORT**; `actual_rows=5`                                                    |
| **EXPLAIN**           | 2           | `sql: SELECT O.ID, O.CREATED_AT, U.EMAIL FROM ORDERS O JOIN USERS U ON U.ID=O.USER_ID WHERE U.COUNTRY='SV' ORDER BY O.CREATED_AT DESC LIMIT 10;` | ANALYZE ON, BUFFERS ON, TIMING ON          | —                                                                                                                                                                                                                                                                                                                                                                           | Join (**NESTED LOOP / HASH JOIN**) y orden por índice si existe                                       |
| **SLOW QUERIES**      | 1           | `top: 10`                                                                                                                                        | —                                          | **Reset (opcional):**<br>`docker exec -it mcp-postgres psql -U mcp -d mcpdb -c "SELECT PG_STAT_STATEMENTS_RESET();"`<br><br>**Carga ligera:**<br>`docker exec -it mcp-postgres psql -U mcp -d mcpdb -c "DO $$ BEGIN FOR i IN 1..400 LOOP PERFORM O.ID FROM ORDERS O JOIN USERS U ON U.ID=U.ID WHERE U.COUNTRY='SV' ORDER BY O.CREATED_AT DESC LIMIT 10; END LOOP; END $$;"` | Lista de queries con `calls`, `mean_ms`, `rows` (requiere **pg_stat_statements**)                     |
| **SLOW QUERIES**      | 2           | `top: 10`                                                                                                                                        | —                                          | **Carga en USERS:**<br>`docker exec -it mcp-postgres psql -U mcp -d mcpdb -c "DO $$ BEGIN FOR i IN 1..600 LOOP PERFORM 1 FROM USERS WHERE COUNTRY='GT' ORDER BY EMAIL LIMIT 5; END LOOP; END $$;"`                                                                                                                                                                          | Entradas normalizadas tipo `SELECT … WHERE COUNTRY=$? ORDER BY … LIMIT $?` con muchos **calls**       |
| **N+1 SUSPICIONS**    | 1           | `min_calls: 500`<br>`max_avg_rows: 2`<br>`min_mean_ms: 0.02`                                                                                     | —                                          | **Carga N+1 “más lenta”:**<br>`docker exec -it mcp-postgres psql -U mcp -d mcpdb -c "DO $$ DECLARE r RECORD; BEGIN FOR r IN SELECT ID FROM ORDERS ORDER BY ID DESC LIMIT 2000 LOOP PERFORM 1 FROM ORDER_ITEMS WHERE ORDER_ID = r.ID ORDER BY PRODUCT LIMIT 1; END LOOP; END $$;"`                                                                                           | Detecta plantilla SELECT … FROM users WHERE id = ? FOR KEY SHARE (bajo avg_rows ≈ 1, mean_ms ≥ 0.002) |
| **N+1 SUSPICIONS**    | 2           | `min_calls: 150`<br>`max_avg_rows: 3`<br>`min_mean_ms: 0.001`                                                                                    | —                                          | **Carga N+1 básica:**<br>`docker exec -it mcp-postgres psql -U mcp -d mcpdb -c "DO $$ DECLARE r RECORD; BEGIN FOR r IN SELECT ID FROM ORDERS ORDER BY ID DESC LIMIT 2000 LOOP PERFORM 1 FROM ORDER_ITEMS WHERE ORDER_ID = r.ID LIMIT 1; END LOOP; END $$;"`                                                                                                                 | Detecta N+1                                                                                           |
| **INDEX SUGGESTIONS** | 1           | `table: public.orders`<br>`sample_sql:`<br>`SELECT ID, CREATED_AT FROM ORDERS WHERE USER_ID = 12345 ORDER BY CREATED_AT DESC LIMIT 50;`          | validate_with_hypopg ON (si tienes hypopg) | **Habilitar HypoPG (una vez):**<br>`docker exec -it mcp-postgres psql -U mcp -d mcpdb -c "CREATE EXTENSION IF NOT EXISTS HYPOPG;"`                                                                                                                                                                                                                                          | Sugerencia esperable: **ON public.orders (user_id, created_at DESC) [INCLUDE (id)]**                  |
| **INDEX SUGGESTIONS** | 2           | `table: public.users`<br>`sample_sql:`<br>`SELECT ID, EMAIL FROM USERS WHERE COUNTRY = 'GT' ORDER BY EMAIL LIMIT 100;`                           | validate_with_hypopg ON                    | —                                                                                                                                                                                                                                                                                                                                                                           | Sugerencia esperable en `public.users (country, email)`                                               |

