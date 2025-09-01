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

## 3) Exponer tu MCP con un túnel público — Terminal C

# si no lo tienes: winget install Cloudflare.cloudflared

```bash
cloudflared tunnel --url http://127.0.0.1:8765
```

Te dará una URL pública tipo: https://<algo>.trycloudflare.com

### Comparte esta URL con el INTEGRATOR (su chatbot la usará como endpoint MCP).

### El INTEGRATOR no necesita acceso a tu Postgres ni correr Docker; solo consumirá tu MCP por HTTP.
