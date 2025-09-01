#  MCP POSTGRESQL PROFILER SERVER (FASTMCP)
#  HERRAMIENTAS... CONNECT, EXPLAIN, SLOW_QUERIES, N_PLUS_ONE_SUSPICIONS, INDEX_SUGGESTIONS (CON VALIDACIÓN OPCIONAL HYOPOG)
# ANDRE MARROQUIN

import argparse
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
import sqlparse
import pandas as pd

import psycopg
from psycopg.rows import dict_row
from mcp.server.fastmcp import FastMCP, Context
from mcp.server.session import ServerSession

# estados globales (conexión actual)
CURRENT_CONN: Optional[psycopg.Connection] = None
CURRENT_DSN: Optional[str] = None

# utiles para análisis SQL
EQUALITY_OPS = {"=", "IN", "IS"}
RANGE_OPS = {">", "<", ">=", "<="}


# normaliza SQL para agrupar plantillas
def normalize_sql(sql: str) -> str:
    formatted = sqlparse.format(sql, keyword_case="lower", strip_comments=True)
    # remplazo simple de literales
    formatted = re.sub(r"\'[^']*\'", "?", formatted)
    formatted = re.sub(r"\b\d+(\.\d+)?\b", "?", formatted)
    return re.sub(r"\s+", " ", formatted).strip()


# extrae columnas de igualdad, rango y order by
def extract_predicates_and_order(sql: str) -> Tuple[List[str], List[str], List[str]]:
    text = sqlparse.format(sql, keyword_case="lower", strip_comments=True)
    # cclausala wheere
    where_match = re.search(r"\bwhere\b(.+?)(\border\b|\blimit\b|$)", text, flags=re.S)
    where = where_match.group(1) if where_match else ""
    # joins
    on_parts = re.findall(r"\bon\b\s+(.+?)(?:\bjoin\b|\bwhere\b|$)", text, flags=re.S)
    predicates = " ".join([where] + on_parts)

    eq_cols: List[str] = []
    range_cols: List[str] = []

    # captura columnas y operadores
    for col, op in re.findall(r"([a-z_][\w\.]*)\s*(=|in|is|>=|<=|>|<)", predicates):
        if op in {"=", "in", "is"}:
            if col not in eq_cols:
                eq_cols.append(col)
        else:
            if col not in range_cols:
                range_cols.append(col)

    # order by
    order = []
    ob = re.search(r"\border\s+by\s+(.+?)(\blimit\b|$)", text, flags=re.S)
    if ob:
        cols = ob.group(1)
        # separa por comas y captura nombres de columnas
        for piece in cols.split(","):
            m = re.search(r"([a-z_][\w\.]*)", piece.strip())
            if m:
                c = m.group(1)
                if c not in order:
                    order.append(c)

    return eq_cols, range_cols, order


# construye lista de columnas para índice compuesto
def build_composite_index(
    eq_cols: List[str], range_cols: List[str], order_cols: List[str]
) -> List[str]:
    ordered = []
    for col in eq_cols + range_cols + order_cols:
        if col not in ordered:
            ordered.append(col)
    return ordered


# recorre árbol JSON de explain para encontrar nodo dominante
def traverse_plan_for_hotspots(
    plan_node: Dict[str, Any], analyze: bool
) -> Dict[str, Any]:
    key_time = "Actual Total Time" if analyze else "Total Cost"
    best = {
        "node_type": plan_node.get("Node Type", "Unknown"),
        "metric": plan_node.get(key_time, 0.0),
        "relation": plan_node.get("Relation Name"),
        "index_name": plan_node.get("Index Name"),
    }
    for child in plan_node.get("Plans", []) or []:
        cand = traverse_plan_for_hotspots(child, analyze)
        if cand["metric"] > best["metric"]:
            best = cand
    return best


# verifica si un plan usa un índice por nombre
def plan_uses_index(plan_node: Dict[str, Any], idx_name: str) -> bool:
    if plan_node.get("Index Name") == idx_name:
        return True
    for child in plan_node.get("Plans", []) or []:
        if plan_uses_index(child, idx_name):
            return True
    return False


# conecta a Postgres y retorna conexión + metadatos
def _connect_internal(dsn: str) -> Tuple[psycopg.Connection, Dict[str, Any]]:
    conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    with conn.cursor() as cur:
        cur.execute("select version()")
        version = cur.fetchone()["version"]
        cur.execute("select extname from pg_extension")
        installed = {r["extname"] for r in cur.fetchall()}
        cur.execute("select name from pg_available_extensions")
        available = {r["name"] for r in cur.fetchall()}
    meta = {
        "server_version": version,
        "extensions_installed": sorted(installed),
        "extensions_available": sorted(available),
    }
    return conn, meta


# verifica si una extensión está instalada
def _pg_has_extension(conn: psycopg.Connection, name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("select 1 from pg_extension where extname=%s", (name,))
        return cur.fetchone() is not None


# intenta crear una extensión (puede fallar si no hay privilegios)
def _pg_try_create_extension(conn: psycopg.Connection, name: str) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute(f'create extension if not exists "{name}"')
        return True
    except Exception:
        return False


# definición del MCP y contexto
mcp = FastMCP("PG Profiler MCP")


# tipado de contexto (no usado en MVP)
@dataclass
class AppCtx:
    placeholder: str = "OK"


# tool connect(dsn)
@mcp.tool()
def connect(dsn: str) -> Dict[str, Any]:
    # abre conexion a Postgres
    global CURRENT_CONN, CURRENT_DSN
    if CURRENT_CONN:
        try:
            CURRENT_CONN.close()
        except Exception:
            pass
        CURRENT_CONN = None
    conn, meta = _connect_internal(dsn)
    CURRENT_CONN = conn
    CURRENT_DSN = dsn

    # dectar pg_stat_statements
    HAS_PGSS = _pg_has_extension(conn, "pg_stat_statements")
    if not HAS_PGSS and "pg_stat_statements" in meta["extensions_available"]:
        _pg_try_create_extension(conn, "pg_stat_statements")
        HAS_PGSS = _pg_has_extension(conn, "pg_stat_statements")

    # detectar hypopg
    HAS_HYPO = _pg_has_extension(conn, "hypopg")
    if not HAS_HYPO and "hypopg" in meta["extensions_available"]:
        _pg_try_create_extension(conn, "hypopg")
        HAS_HYPO = _pg_has_extension(conn, "hypopg")

    meta.update({"pg_stat_statements": HAS_PGSS, "hypopg": HAS_HYPO})
    return {"connected": True, "dsn": dsn, "meta": meta}


# tools de mcp
@mcp.tool()
# tool explain(sql, analyze, buffers, timing) devuelve plan + resumen
def explain(
    sql: str, analyze: bool = False, buffers: bool = True, timing: bool = True
) -> Dict[str, Any]:
    if not CURRENT_CONN:
        raise RuntimeError("NO HAY CONEXIÓN ACTIVA. LLAMA PRIMERO A connect(dsn).")
    options = ["FORMAT JSON"]
    if analyze:
        options.append("ANALYZE TRUE")
    if buffers:
        options.append("BUFFERS TRUE")
    if timing:
        options.append("TIMING TRUE")

    query = f"EXPLAIN ({', '.join(options)}) {sql}"
    with CURRENT_CONN.cursor() as cur:
        cur.execute(query)
        row = cur.fetchone()
        plan_json = row[0] if isinstance(row, (list, tuple)) else row["QUERY PLAN"]
        root = plan_json[0]["Plan"]

    hotspot = traverse_plan_for_hotspots(root, analyze)
    summary = {
        "dominant_node": hotspot,
        "plan_width": root.get("Plan Width"),
        "startup_cost": root.get("Startup Cost") if not analyze else None,
        "total_cost": root.get("Total Cost") if not analyze else None,
        "actual_rows": root.get("Actual Rows") if analyze else None,
        "actual_total_time_ms": root.get("Actual Total Time") if analyze else None,
    }
    return {"plan": plan_json, "summary": summary}


# tool slow_queries(top)
@mcp.tool()
# lista slow queries desde pg_stat_statements (top) devuelve lista de queries lentas
def slow_queries(top: int = 20) -> Dict[str, Any]:
    if not CURRENT_CONN:
        raise RuntimeError("NO HAY CONEXIÓN ACTIVA. LLAMA PRIMERO A connect(dsn).")

    with CURRENT_CONN.cursor() as cur:
        stats_reset = None
        try:
            cur.execute("select stats_reset from pg_stat_statements_info")
            r = cur.fetchone()
            if r:
                stats_reset = r.get("stats_reset")
        except Exception:
            pass

        rows = []
        # intenta esquema nuevo (pg16+)
        try:
            cur.execute(
                """
                select queryid, query, calls,
                       total_exec_time as total_ms,
                       mean_exec_time  as mean_ms,
                       rows
                from pg_stat_statements
                order by mean_exec_time desc
                limit %s
            """,
                (top,),
            )
            rows = cur.fetchall()
        except Exception:
            # Fallback a versiones antiguas total_time / mean_time
            try:
                cur.execute(
                    """
                    select queryid, query, calls,
                           total_time as total_ms,
                           mean_time  as mean_ms,
                           rows
                    from pg_stat_statements
                    order by mean_time desc
                    limit %s
                """,
                    (top,),
                )
                rows = cur.fetchall()
            except Exception as e2:
                return {
                    "pg_stat_statements": False,
                    "warning": "pg_stat_statements no instalado o sin permisos",
                    "error": str(e2),
                }

    results = []
    for r in rows:
        normalized = normalize_sql(r.get("query") or "")
        results.append(
            {
                "queryid": str(r.get("queryid")),
                "calls": int(r.get("calls") or 0),
                "rows": int(r.get("rows") or 0),
                "total_ms": float(r.get("total_ms") or 0.0),
                "mean_ms": float(r.get("mean_ms") or 0.0),
                "normalized": normalized[:500],
            }
        )

    return {
        "pg_stat_statements": True,
        "stats_reset": str(stats_reset) if stats_reset else None,
        "top": results,
    }


# tool n_plus_one_suspicions(min_calls, max_avg_rows, min_mean_ms) devuelve sospechas N+1
@mcp.tool()
def n_plus_one_suspicions(
    min_calls: int = 20, max_avg_rows: float = 3.0, min_mean_ms: float = 0.5
) -> Dict[str, Any]:
    if not CURRENT_CONN:
        raise RuntimeError("NO HAY CONEXIÓN ACTIVA. LLAMA PRIMERO A connect(dsn).")

    with CURRENT_CONN.cursor() as cur:
        rows = []
        try:
            cur.execute(
                """
                select query, calls,
                       mean_exec_time as mean_ms,
                       nullif(rows,0)::float / nullif(calls,0) as avg_rows
                from pg_stat_statements
                where calls >= %s
                order by calls desc
                limit 500
            """,
                (min_calls,),
            )
            rows = cur.fetchall()
        except Exception:
            # Fallback a versiones antiguas
            try:
                cur.execute(
                    """
                    select query, calls,
                           mean_time as mean_ms,
                           nullif(rows,0)::float / nullif(calls,0) as avg_rows
                    from pg_stat_statements
                    where calls >= %s
                    order by calls desc
                    limit 500
                """,
                    (min_calls,),
                )
                rows = cur.fetchall()
            except Exception as e2:
                return {
                    "pg_stat_statements": False,
                    "warning": "pg_stat_statements no instalado o sin permisos",
                    "error": str(e2),
                }

    suspects = []
    for r in rows:
        avg_rows = float(r.get("avg_rows") or 0.0)
        mean_ms = float(r.get("mean_ms") or 0.0)
        if avg_rows <= max_avg_rows and mean_ms >= min_mean_ms:
            suspects.append(
                {
                    "normalized": normalize_sql(r.get("query") or "")[:500],
                    "calls": int(r.get("calls") or 0),
                    "avg_rows": avg_rows,
                    "mean_ms": mean_ms,
                }
            )

    suspects.sort(key=lambda x: (x["calls"], x["mean_ms"]), reverse=True)
    return {"pg_stat_statements": True, "suspicions": suspects[:50]}


# propone índices compuestos basados en heurísticas
# igualdad -> rango -> order by
# si hay hypopg, crea índice hipotético y verifica si el plan lo usa
@mcp.tool()
def index_suggestions(
    table: Optional[str] = None,
    sample_sql: Optional[str] = None,
    validate_with_hypopg: bool = True,
) -> Dict[str, Any]:
    if not CURRENT_CONN:
        raise RuntimeError("NO HAY CONEXIÓN ACTIVA. LLAMA PRIMERO A connect(dsn).")

    def _base_name(ident: str) -> str:
        ident = ident.strip().strip('"')
        return ident.split(".")[-1]

    def _same_table(a: str, b: str) -> bool:
        return _base_name(a).lower() == _base_name(b).lower()

    def _find_target_alias(sql: str, target_table: str) -> Optional[str]:
        KEYWORDS = {
            "where",
            "on",
            "using",
            "group",
            "order",
            "limit",
            "offset",
            "union",
            "intersect",
            "except",
            "join",
            "inner",
            "left",
            "right",
            "full",
            "cross",
            "natural",
            "window",
            "having",
            "values",
            "returning",
            "for",
            "lock",
            "and",
            "or",
            "not",
            "with",
        }
        pat = re.compile(
            r'\b(from|join)\s+([a-z0-9_\."]+)(?:\s+(?:as\s+)?([a-z_][a-z0-9_]*))?', re.I
        )
        for m in pat.finditer(sql):
            tbl, alias = m.group(2), m.group(3)
            if _same_table(tbl, target_table):
                if alias and alias.lower() not in KEYWORDS:
                    return alias
                return _base_name(tbl)
        return None

    def _belongs_to_target(
        col: str, target_alias: Optional[str], target_table: str
    ) -> bool:
        c = col.strip().strip('"')
        parts = [p for p in re.split(r"\.", c) if p]
        if len(parts) == 1:
            return True
        qualifier = parts[-2]
        if target_alias and qualifier.lower() == target_alias.lower():
            return True
        if _same_table(qualifier, target_table):
            return True
        return False

    def _col_only(col: str) -> str:
        c = col.strip().strip('"')
        return c.split(".")[-1]

    def _dedupe(seq):
        seen = set()
        out = []
        for x in seq:
            key = x if isinstance(x, str) else tuple(sorted(x.items()))
            if key not in seen:
                seen.add(key)
                out.append(x)
        return out

    suggestions: List[Dict[str, Any]] = []
    explanation: List[Dict[str, Any]] = []

    if not sample_sql:
        return {"explanation": explanation, "suggestions": suggestions}

    sql = sample_sql.strip()
    target_alias = _find_target_alias(sql, table) if table else None

    eq_cols: List[str] = []
    range_cols: List[str] = []
    order_cols: List[Dict[str, str]] = []
    try:
        eq0, rg0, ob0 = extract_predicates_and_order(sql)
        # Filtrar por tabla objetivo y normalizar
        eq_cols = [
            _col_only(c)
            for c in eq0
            if _belongs_to_target(c, target_alias, table or "")
        ]
        range_cols = [
            _col_only(c)
            for c in rg0
            if _belongs_to_target(c, target_alias, table or "")
        ]
        for oc in ob0:
            if isinstance(oc, dict):
                c = oc.get("column") or oc.get("col") or ""
                d = (oc.get("direction") or "asc").lower()
                if c and _belongs_to_target(c, target_alias, table or ""):
                    order_cols.append(
                        {
                            "column": _col_only(c),
                            "direction": "desc" if d == "desc" else "asc",
                        }
                    )
            else:
                c = str(oc)
                if _belongs_to_target(c, target_alias, table or ""):
                    order_cols.append({"column": _col_only(c), "direction": "asc"})
    except Exception:
        pass

    # Fallback regex si no se detectó nada
    if not eq_cols and not range_cols and not order_cols:
        for m in re.finditer(
            r'([a-z_][a-z0-9_\."]*)\s*=\s*([a-z_][a-z0-9_\."]*)', sql, re.I
        ):
            left, right = m.group(1), m.group(2)
            if table:
                if _belongs_to_target(left, target_alias, table):
                    eq_cols.append(_col_only(left))
                elif _belongs_to_target(right, target_alias, table):
                    eq_cols.append(_col_only(right))
            else:
                eq_cols.append(_col_only(left))
        for m in re.finditer(
            r'([a-z_][a-z0-9_\."]*)\s*=\s*(?:\'[^\']*\'|\$\d+|\?|\d+(?:\.\d+)?|true|false|null)',
            sql,
            re.I,
        ):
            left = m.group(1)
            if not table or _belongs_to_target(left, target_alias, table):
                eq_cols.append(_col_only(left))
        for m in re.finditer(r'([a-z_][a-z0-9_\."]*)\s*(>=|>|<=|<)\s*', sql, re.I):
            col = m.group(1)
            if not table or _belongs_to_target(col, target_alias, table):
                range_cols.append(_col_only(col))
        for m in re.finditer(r'([a-z_][a-z0-9_\."]*)\s+between\s+', sql, re.I):
            col = m.group(1)
            if not table or _belongs_to_target(col, target_alias, table):
                range_cols.append(_col_only(col))
        # order by
        m = re.search(r"\border\s+by\s+(.+?)(?:\blimit\b|$)", sql, re.I | re.S)
        if m:
            ob_list = m.group(1)
            for term in re.split(r"\s*,\s*", ob_list.strip()):
                mm = re.match(r'([a-z_][a-z0-9_\."]*)\s*(asc|desc)?', term, re.I)
                if not mm:
                    continue
                col, direction = mm.group(1), (mm.group(2) or "asc").lower()
                if not table or _belongs_to_target(col, target_alias, table):
                    order_cols.append(
                        {
                            "column": _col_only(col),
                            "direction": "desc" if direction == "desc" else "asc",
                        }
                    )

    eq_cols = _dedupe(eq_cols)
    range_cols = _dedupe(range_cols)
    order_cols = _dedupe(order_cols)

    # Orden final de columnas del índice igualdades -> rangos -> order by
    ordered_cols: List[str] = []
    ordered_cols += eq_cols
    ordered_cols += [c for c in range_cols if c not in ordered_cols]
    for oc in order_cols:
        col = oc["column"]
        if col not in ordered_cols:
            ordered_cols.append(f"{col} DESC" if oc.get("direction") == "desc" else col)

    # Explicación de lo detectado
    explanation.append(
        {
            "eq_cols": eq_cols,
            "range_cols": range_cols,
            "order_cols": order_cols,
            "target_alias": target_alias,
        }
    )

    # Construir sugerencia y validar opcionalmente con HypoPG
    if table and ordered_cols:
        idx_cols_expr = ", ".join(ordered_cols)
        create_sql = f"CREATE INDEX ON {table} ({idx_cols_expr})"
        suggestion: Dict[str, Any] = {
            "table": table,
            "columns_ordered": ordered_cols,
            "create_index_sql": create_sql,
        }

        if validate_with_hypopg and _pg_has_extension(CURRENT_CONN, "hypopg"):
            try:
                with CURRENT_CONN.cursor() as cur:
                    cur.execute("select * from hypopg_create_index(%s)", (create_sql,))
                    res = cur.fetchone()
                    hypo_name = res.get("indexname") if res else None

                    cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")
                    plan_json = cur.fetchone()[0]
                    root = plan_json[0]["Plan"]
                    used = plan_uses_index(root, hypo_name) if hypo_name else False

                    suggestion["hypopg_index"] = hypo_name
                    suggestion["plan_uses_index"] = bool(used)

                    cur.execute("select hypopg_reset()")
            except Exception as e:
                # Solo incluir hypopg_error cuando realmente hay error
                suggestion["hypopg_error"] = str(e)

        suggestions.append(suggestion)

    return {"explanation": explanation, "suggestions": suggestions}


# JSON-RPC SHIM expone POST / con  initialize, tools/list, tools/call
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn


# JSON-RPC 2.0 plano en POST / para máxima interoperabilidad.
# initialize / notifications/initialized
# tools/list (y tools.list): devuelve herramientas con inputSchema detallado
# tools/call  (y tools.call): invoca las funciones MCP reales
def build_jsonrpc_app() -> FastAPI:
    app = FastAPI(title="PG Profiler MCP (JSON-RPC)")
    # Mapa de herramientas reales a funciones Python
    TOOLS_MAP = {
        "connect": connect,
        "explain": explain,
        "slow_queries": slow_queries,
        "n_plus_one_suspicions": n_plus_one_suspicions,
        "index_suggestions": index_suggestions,
    }

    def _ok(rid, result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _err(rid, code, msg):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}

    @app.post("/")
    async def jsonrpc_entry(request: Request):
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(_err(None, -32700, "Parse error"), status_code=400)

        rid = payload.get("id")
        method = (payload.get("method") or "").strip()
        params = payload.get("params") or {}

        # handshake sin estado
        if method == "initialize":
            return JSONResponse(_ok(rid, {"capabilities": {}}))
        if method in ("notifications/initialized", "initialized"):
            return JSONResponse(_ok(rid, {}))

        # listar herramientas
        if method in ("tools/list", "tools.list"):
            tools = [
                {
                    "name": "connect",
                    "description": "Abre una conexión a PostgreSQL con un DSN.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "dsn": {
                                "type": "string",
                                "description": "DSN de PostgreSQL. Ej: postgresql://user:pass@host:5432/db",
                            }
                        },
                        "required": ["dsn"],
                        "additionalProperties": False,
                    },
                },
                {
                    "name": "explain",
                    "description": "Ejecuta EXPLAIN/EXPLAIN ANALYZE en formato JSON.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "sql": {
                                "type": "string",
                                "description": "Consulta SQL completa a explicar.",
                            },
                            "analyze": {
                                "type": "boolean",
                                "description": "Si true, usa EXPLAIN ANALYZE.",
                                "default": True,
                            },
                            "buffers": {
                                "type": "boolean",
                                "description": "Incluir métricas de buffers.",
                                "default": True,
                            },
                            "timing": {
                                "type": "boolean",
                                "description": "Incluir métricas de tiempo por nodo.",
                                "default": True,
                            },
                        },
                        "required": ["sql"],
                        "additionalProperties": False,
                    },
                },
                {
                    "name": "slow_queries",
                    "description": "Lista queries lentas desde pg_stat_statements (top N por mean_exec_time).",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "top": {
                                "type": "integer",
                                "description": "Cantidad de filas a devolver.",
                                "default": 20,
                                "minimum": 1,
                                "maximum": 1000,
                            }
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                },
                {
                    "name": "n_plus_one_suspicions",
                    "description": "Heurística para detectar patrón N+1 usando pg_stat_statements.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "min_calls": {
                                "type": "integer",
                                "description": "Mínimo de llamadas por plantilla para considerarla.",
                                "default": 20,
                                "minimum": 1,
                            },
                            "max_avg_rows": {
                                "type": "number",
                                "description": "Umbral máximo de filas promedio por llamada (bajo = sospechoso).",
                                "default": 3.0,
                                "minimum": 0,
                            },
                            "min_mean_ms": {
                                "type": "number",
                                "description": "Umbral mínimo de tiempo medio en ms.",
                                "default": 0.5,
                                "minimum": 0,
                            },
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                },
                {
                    "name": "index_suggestions",
                    "description": "Propone índices compuestos (igualdades→rangos→ORDER BY). Puede validar con HypoPG.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "table": {
                                "type": "string",
                                "description": "Tabla objetivo (schema.qualname). Ej: public.orders",
                            },
                            "sample_sql": {
                                "type": "string",
                                "description": "SQL de ejemplo sobre el que derivar las columnas candidatas.",
                            },
                            "validate_with_hypopg": {
                                "type": "boolean",
                                "description": "Si true, valida con HypoPG (si disponible).",
                                "default": True,
                            },
                        },
                        "required": ["table", "sample_sql"],
                        "additionalProperties": False,
                    },
                },
            ]
            return JSONResponse(_ok(rid, {"tools": tools}))

        # llama a la herramienta de las funciones definidas arriba
        if method in ("tools/call", "tools.call"):
            name = (params.get("name") or "").strip()
            arguments = params.get("arguments") or {}
            fn = TOOLS_MAP.get(name)
            if not fn:
                return JSONResponse(
                    _err(rid, -32601, f"Unknown tool: {name}"), status_code=404
                )
            try:
                result = fn(**arguments)
                return JSONResponse(_ok(rid, result))
            except TypeError as e:
                # error de parámetros
                return JSONResponse(
                    _err(rid, -32602, f"Invalid params: {e}"), status_code=400
                )
            except Exception as e:
                # error interno de la herramienta
                return JSONResponse(
                    _err(rid, -32000, f"Tool error: {e}"), status_code=500
                )

        # metodo no encontrado
        return JSONResponse(
            _err(rid, -32601, f"Method not found: {method}"), status_code=404
        )

    return app


# el main usa argparse para elegir transporte y parámetros http streamable o stdio
def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="PG Profiler MCP Server")
    # Modo HTTP streamable
    parser.add_argument(
        "--http",
        action="store_true",
        help="Transporte HTTP streamable (compatible con MCP Inspector).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host para streamable-http (si usas --http).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Puerto para streamable-http (si usas --http).",
    )

    # Modo JSON-RPC plano
    parser.add_argument(
        "--jsonrpc",
        action="store_true",
        help="Exponer JSON-RPC 2.0 'plano' en POST / (máxima interoperabilidad).",
    )
    parser.add_argument(
        "--rpc-host",
        default="127.0.0.1",
        help="Host para JSON-RPC (si usas --jsonrpc).",
    )
    parser.add_argument(
        "--rpc-port",
        type=int,
        default=8787,
        help="Puerto para JSON-RPC (si usas --jsonrpc).",
    )

    # Forzar stdio explícito
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Forzar transporte STDIO (por defecto si no se pasan flags).",
    )

    args = parser.parse_args()

    # No permitimos mezclar http y jsonrpc en un solo proceso
    if args.http and args.jsonrpc:
        print(
            "Error: no combines --http y --jsonrpc en el mismo proceso. "
            "Ejecuta cada modo en una terminal/proceso distinto."
        )
        return

    # JSON-RPC plano
    if args.jsonrpc:
        # debe estar definida arriba del archivo (shim JSON-RPC)
        app = build_jsonrpc_app()
        uvicorn.run(app, host=args.rpc_host, port=args.rpc_port)
        return

    # HTTP streamable Inspector
    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
        return

    # STDIO
    mcp.run()
    return


if __name__ == "__main__":
    main()
