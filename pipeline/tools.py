"""
FahMai Agentic Pipeline — Tool Layer

Provides the agent with tools to:
1. Query CSV tables via DuckDB SQL
2. Search structured documents (memos, minutes, emails, policies)
3. Search unstructured chat transcripts (LINE OA + LINE WORKS)
4. Read individual documents

Unstructured data strategy (production-grade, hybrid):
- Small structured docs (memos/minutes/emails/policies): indexed in-memory, keyword + date search
- Massive chat corpus (53k+ files): pre-filtered by date from filename, then keyword-greped
  This avoids costly embeddings on 53k files while maintaining precision via temporal filtering.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from config import (
    DATA_DIR,
    DOCS_DIR,
    LOGS_DIR,
    MAX_CHAT_SEARCH_RESULTS,
    MAX_DOC_SEARCH_RESULTS,
    REPORTS_DIR,
    SQL_RESULT_ROW_LIMIT,
    TABLES_DIR,
)


# ---------------------------------------------------------------------------
# DuckDB connection (lazy init)
# ---------------------------------------------------------------------------

_db_conn: duckdb.DuckDBPyConnection | None = None


def _get_db() -> duckdb.DuckDBPyConnection:
    global _db_conn
    if _db_conn is None:
        _db_conn = duckdb.connect(":memory:")
        _load_all_tables(_db_conn)
    return _db_conn


def _load_all_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """Load every CSV in tables/ into DuckDB as a named table."""
    for csv_path in sorted(TABLES_DIR.glob("*.csv")):
        table_name = csv_path.stem
        try:
            conn.execute(
                f"CREATE OR REPLACE TABLE \"{table_name}\" AS "
                f"SELECT * FROM read_csv_auto('{csv_path}', header=true, all_varchar=true)"
            )
        except Exception:
            # Some CSVs may have odd quoting — try with quote detection off
            conn.execute(
                f"CREATE OR REPLACE TABLE \"{table_name}\" AS "
                f"SELECT * FROM read_csv_auto('{csv_path}', header=true, all_varchar=true, ignore_errors=true)"
            )


# ---------------------------------------------------------------------------
# Tool: explore_schema (consolidated list_tables + get_table_schema)
# ---------------------------------------------------------------------------

def explore_schema(table_name: str = "", include_sample: bool = False) -> str:
    """Explore the database schema. Without parameters, lists all tables with row/column counts.
    With table_name, shows detailed column schema and optional sample rows.
    Use this tool before writing SQL queries to understand available data."""
    conn = _get_db()

    if not table_name:
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY table_name"
        ).fetchall()
        lines = ["Available tables:\n"]
        for (name,) in tables:
            row_count = conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            cols = [c[0] for c in conn.execute(f'DESCRIBE "{name}"').fetchall()]
            prefix = "DIM" if name.startswith("DIM") else "FACT" if name.startswith("FACT") else "  "
            lines.append(f"  [{prefix}] {name}  ({row_count} rows, {len(cols)} cols)")
        return "\n".join(lines)

    try:
        rows = conn.execute(f'DESCRIBE "{table_name}"').fetchall()
        cols = [f"  {r[0]:<40s} {r[1]}" for r in rows]
        lines = [f"Schema for {table_name} ({len(cols)} columns):", ""]
        lines.extend(cols)

        if include_sample:
            try:
                sample = conn.execute(f'SELECT * FROM "{table_name}" LIMIT 3').fetchall()
                col_names = [r[0] for r in rows]
                lines.append("")
                lines.append(f"Sample rows (showing up to 3):")
                for s_row in sample:
                    sample_parts = [f"  {col_names[i]}: {s_row[i]}" for i in range(len(col_names))]
                    lines.append("---")
                    lines.extend(sample_parts)
            except Exception:
                lines.append("")
                lines.append("(Could not fetch sample rows)")

        return "\n".join(lines)
    except Exception as e:
        return f"Table '{table_name}' not found. Use explore_schema() to list available tables. Error: {e}"


# ---------------------------------------------------------------------------
# Tool: query_sql (formerly query_csv)
# ---------------------------------------------------------------------------

def query_sql(sql: str, format: str = "table") -> str:
    """Execute a SQL query against the FahMai data warehouse (DuckDB).
    All dimension and fact tables are pre-loaded. Use standard SQL syntax.
    Column names are case-sensitive and match CSV headers exactly.
    Results limited to 200 rows by default. Use LIMIT/OFFSET for more.
    This is the primary tool for any data lookup, aggregation, or analysis.
    """
    conn = _get_db()
    try:
        result = conn.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchmany(SQL_RESULT_ROW_LIMIT + 1)
        truncated = len(rows) > SQL_RESULT_ROW_LIMIT
        if truncated:
            rows = rows[:SQL_RESULT_ROW_LIMIT]

        if not rows:
            return "(empty result — no rows returned)"

        if format == "csv":
            out = [",".join(columns)]
            for r in rows:
                out.append(",".join(f'"{v}"' for v in r))
            out.append(f"\n({len(rows)} rows" + (" — truncated" if truncated else "") + ")")
            return "\n".join(out)

        if format == "json":
            import json as _json
            data = [{col: r[i] for i, col in enumerate(columns)} for r in rows]
            result_obj = {"rows": data, "count": len(rows), "truncated": truncated}
            return _json.dumps(result_obj, ensure_ascii=False, indent=2)

        # Default: markdown table
        col_widths = [max(len(str(c)), max(len(str(r[i])) for r in rows)) for i, c in enumerate(columns)]
        header = "| " + " | ".join(c.ljust(col_widths[i]) for i, c in enumerate(columns)) + " |"
        sep = "|-" + "-|-".join("-" * col_widths[i] for i in range(len(columns))) + "-|"
        data_lines = [
            "| " + " | ".join(str(r[i]).ljust(col_widths[i]) for i in range(len(columns))) + " |"
            for r in rows
        ]
        out = [header, sep] + data_lines
        out.append(f"\n({len(rows)} rows" + (" — truncated, use LIMIT/OFFSET for more" if truncated else "") + ")")
        return "\n".join(out)
    except Exception as e:
        err = str(e)
        hint = ""
        if "not found" in err.lower():
            hint = "Hint: Use explore_schema() to list available tables and check column names."
        elif "column" in err.lower() and "not found" in err.lower():
            hint = "Hint: Use explore_schema(table_name='...') to check column names. Column names are case-sensitive."
        return f"SQL Error: {err}\n{hint}".rstrip()


# ---------------------------------------------------------------------------
# Document index (memos, minutes, emails, policies, product specs, reports)
# ---------------------------------------------------------------------------

_doc_index: list[dict[str, Any]] | None = None


def _build_doc_index() -> list[dict[str, Any]]:
    """Build an in-memory index of all structured documents."""
    global _doc_index
    if _doc_index is not None:
        return _doc_index

    _doc_index = []
    doc_sources = [
        (DOCS_DIR / "memo", "memo"),
        (DOCS_DIR / "minutes", "minutes"),
        (DOCS_DIR / "email", "email"),
        (DOCS_DIR / "l1_kb" / "policies", "policy"),
        (DOCS_DIR / "l1_kb" / "products", "product_spec"),
        (DOCS_DIR / "l1_kb" / "store_info", "store_info"),
    ]
    for folder_path in REPORTS_DIR.glob("*/*.md"):
        doc_sources.append((str(folder_path.parent), f"report/{folder_path.parent.name}"))

    for folder, doc_kind in doc_sources:
        if not Path(folder).exists():
            continue
        for file_path in sorted(Path(folder).glob("*.md")):
            try:
                content = file_path.read_text(encoding="utf-8")
                # Extract date hints from filename and content
                date_hint = _extract_date_from_filename(file_path.name)
                _doc_index.append({
                    "path": str(file_path),
                    "kind": doc_kind,
                    "filename": file_path.name,
                    "date": date_hint,
                    "content": content[:5000],  # Store first 5k chars for search preview
                    "full_content": content,
                })
            except Exception:
                continue

    return _doc_index


def _extract_date_from_filename(filename: str) -> str | None:
    """Try to extract YYYY-MM-DD from a filename."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    return m.group(1) if m else None


def search_documents(
    keywords: str = "",
    date_from: str = "",
    date_to: str = "",
    doc_kind: str = "all",
    return_content: bool = False,
) -> str:
    """Search structured documents: memos, meeting minutes, emails, policies,
    product specs, and reports. Use for finding company policies, internal
    communications, or documentation. Supports keyword search, date filtering,
    and document type filtering. Set return_content=true to get full document text.

    Parameters:
    - keywords: space-separated search terms (case-insensitive, OR logic). Leave empty to match all.
    - date_from / date_to: filter by document date (YYYY-MM-DD format)
    - doc_kind: filter by type — 'memo', 'minutes', 'email', 'policy', 'product_spec', 'report', 'store_info', 'all'
    - return_content: if true, return full content of first matching document; if false, return list with previews
    """
    idx = _build_doc_index()
    results = idx.copy()

    if doc_kind and doc_kind != "all":
        results = [d for d in results if d["kind"] == doc_kind]
    if date_from:
        results = [d for d in results if d.get("date") and d["date"] >= date_from]
    if date_to:
        results = [d for d in results if d.get("date") and d["date"] <= date_to]
    if keywords:
        kw_list = [k.lower() for k in keywords.split()]
        filtered = []
        for d in results:
            content_lower = d["content"].lower()
            if any(kw in content_lower for kw in kw_list):
                filtered.append(d)
        results = filtered

    if not results:
        return (
            f"No documents found. "
            f"(Searched: kind={doc_kind}, date={date_from or 'any'}→{date_to or 'any'}, keywords='{keywords}')"
            f"\nHint: Try broader date range or different keywords. Use search_documents(doc_kind='all') to see all documents."
        )

    if return_content:
        d = results[0]
        content = d["full_content"][:8000]
        return (
            f"[{d['kind']}] {d['filename']}"
            + (f"\nDate: {d['date']}" if d.get("date") else "")
            + f"\n\n{content}"
        )

    results = results[:MAX_DOC_SEARCH_RESULTS]
    lines = [f"Found {len(results)} documents:"]
    for d in results:
        preview = d["content"][:300].replace("\n", " ").strip()
        lines.append(f"\n  [{d['kind']}] {d['filename']}")
        if d.get("date"):
            lines.append(f"    Date: {d['date']}")
        lines.append(f"    Preview: {preview}...")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Chat search (LINE OA + LINE WORKS)
# ---------------------------------------------------------------------------

# Pre-built content cache: {source: [(Path, date_str|None, content_lower, content)]}
_chat_content_cache: dict[str, list[tuple]] | None = None


def _build_chat_index() -> dict[str, list[tuple]]:
    """Build an in-memory content cache of all chat files.

    Pre-reads every chat transcript so search_chats() never hits disk during queries.
    Startup cost: ~1.2s extra to read 53k files (~140MB), but each subsequent
    search_chats() call is pure in-memory (saves ~0.7s per undated search).
    """
    global _chat_content_cache
    if _chat_content_cache is not None:
        return _chat_content_cache

    _chat_content_cache = {"line_oa": [], "line_works": []}

    oa_dir = DOCS_DIR / "chat_line_oa"
    if oa_dir.exists():
        for f in sorted(oa_dir.glob("*.md")):
            date_hint = _extract_date_from_filename(f.name)
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                _chat_content_cache["line_oa"].append((f, date_hint, content.lower(), content))
            except Exception:
                continue

    lw_dir = DOCS_DIR / "chat_line_works"
    if lw_dir.exists():
        for f in sorted(lw_dir.glob("*.md")):
            date_hint = _extract_date_from_filename(f.name)
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                _chat_content_cache["line_works"].append((f, date_hint, content.lower(), content))
            except Exception:
                continue

    return _chat_content_cache


def search_chats(
    keywords: str = "",
    date_from: str = "",
    date_to: str = "",
    source: str = "both",
    return_content: bool = False,
    max_results: int = MAX_CHAT_SEARCH_RESULTS,
) -> str:
    """Search LINE OA (customer) and LINE WORKS (internal team) chat transcripts.

    Uses date pre-filtering from filenames for speed, then keyword search.
    Set return_content=true to get full chat text of the first match.
    Use for finding internal discussions, incident reports, customer feedback,
    or explanations for data anomalies.

    The search uses a two-phase approach optimized for 53k+ files:
    1. DATE pre-filter: extracts YYYY-MM-DD from filenames (fast, in-memory)
    2. KEYWORD match: scans pre-loaded content strings (no disk I/O per query)

    Parameters:
    - keywords: space-separated terms (OR logic - any keyword matches)
    - date_from / date_to: YYYY-MM-DD range (uses filename date, fast)
    - source: 'line_oa', 'line_works', or 'both' (default)
    - return_content: if true, return full content of first matching chat; if false, return list with snippets
    - max_results: maximum number of matching files to return (when return_content=false)
    """
    cache = _build_chat_index()

    sources = []
    if source in ("both", "line_oa"):
        sources.append(("line_oa", cache["line_oa"]))
    if source in ("both", "line_works"):
        sources.append(("line_works", cache["line_works"]))

    kw_list = [k.lower() for k in keywords.split()] if keywords else []
    matches = []

    for src_label, entries in sources:
        for fp, date_in_name, content_lower, content in entries:
            # Fast date pre-filter (in-memory string compare, no disk I/O)
            if date_from and (not date_in_name or date_in_name < date_from):
                continue
            if date_to and (not date_in_name or date_in_name > date_to):
                continue

            if not kw_list:
                matches.append((src_label, fp, "", date_in_name, content))
                if len(matches) >= max_results:
                    break
                continue

            # Keyword match against pre-loaded lowercased content (no disk I/O)
            if any(kw in content_lower for kw in kw_list):
                matching_kw = next(kw for kw in kw_list if kw in content_lower)
                snippet = _extract_snippet(content, matching_kw)
                matches.append((src_label, fp, snippet, date_in_name, content))

            if len(matches) >= max_results:
                break
        if len(matches) >= max_results:
            break

    if not matches:
        return (
            f"No chat threads found. "
            f"(source={source}, date={date_from or 'any'}→{date_to or 'any'}, keywords='{keywords}')"
            f"\nHint: Try broader date range or different keywords."
        )

    if return_content:
        src_label, fp, snippet, date_in_name, content = matches[0]
        return (
            f"[{src_label}] {fp.name}"
            + (f"\nDate: {date_in_name}" if date_in_name else "")
            + f"\n\n{content[:10000]}"
        )

    lines = [f"Found {len(matches)} chat threads:\n"]
    for src_label, fp, snippet, date_in_name, _content in matches[:max_results]:
        lines.append(f"  [{src_label}] {fp.name}")
        if date_in_name:
            lines.append(f"    Date: {date_in_name}")
        if snippet:
            lines.append(f"    Snippet: {snippet[:200]}...")
    return "\n".join(lines)


def _extract_snippet(text: str, keyword: str, context_chars: int = 200) -> str:
    """Extract a snippet around the first occurrence of a keyword."""
    idx = text.lower().find(keyword.lower())
    if idx < 0:
        return text[:context_chars]
    start = max(0, idx - context_chars // 2)
    end = min(len(text), idx + context_chars // 2)
    return text[start:end]


# ---------------------------------------------------------------------------
# Tool: lookup_policy
# ---------------------------------------------------------------------------

def lookup_policy(policy_type: str, effective_date: str = "", vendor_id: str = "") -> str:
    """Look up company policies, contract versions, and reference data that change over time.
    Use for questions about refund policies, loyalty program rates, return windows,
    or vendor contracts effective at specific dates. Handles date-range logic automatically.
    """
    conn = _get_db()

    if policy_type in ("refund", "loyalty", "return", "all"):
        where_clauses = []
        if policy_type == "refund":
            where_clauses.append("(LOWER(policy_variable) LIKE '%refund%' OR LOWER(policy_class) = 'signing_authority')")
        elif policy_type == "loyalty":
            where_clauses.append("(LOWER(policy_variable) LIKE '%point%' OR LOWER(policy_variable) LIKE '%loyalty%' OR LOWER(policy_class) = 'membership')")
        elif policy_type == "return":
            where_clauses.append("(LOWER(policy_variable) LIKE '%return%' OR LOWER(policy_class) = 'return')")

        if effective_date:
            where_clauses.append(f"effective_date <= '{effective_date}'")
            where_clauses.append(f"(end_date IS NULL OR end_date >= '{effective_date}')")

        sql = (
            "SELECT policy_version_id, policy_class, policy_variable, "
            "scope_filter, value_numeric, value_text, policy_value_table_ref, "
            "effective_date, end_date "
            "FROM DIM_POLICY_VERSION"
        )
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
        sql += " ORDER BY effective_date DESC"

        try:
            result = conn.execute(sql).fetchall()
        except Exception as e:
            return f"Error querying policies: {e}"

        if not result:
            return f"No {policy_type} policy found" + (f" for date {effective_date}" if effective_date else "") + "."

        lines = [f"Policy: {policy_type}"]
        if effective_date:
            lines.append(f"Effective date: {effective_date}")
        lines.append("")
        for row in result:
            lines.append(f"Version: {row[0]} | Class: {row[1]} | Effective: {row[7]} to {row[8] or 'present'}")
            value = row[4] if row[4] else row[5] if row[5] else f"ref → {row[6]}" if row[6] else "N/A"
            lines.append(f"  {row[2]}: {value}")
            if row[3] and row[3] != "global":
                lines.append(f"  Scope: {row[3]}")
        return "\n".join(lines)

    elif policy_type == "vendor_contract":
        if not vendor_id:
            return "Error: vendor_id parameter is required for vendor_contract policy_type."

        where_clauses = [f"vendor_id = '{vendor_id}'"]
        if effective_date:
            where_clauses.append(f"effective_date <= '{effective_date}'")
            where_clauses.append(f"(end_date IS NULL OR end_date >= '{effective_date}')")

        sql = (
            "SELECT contract_version_id, vendor_id, version_number, "
            "effective_date, end_date, amendment_summary "
            "FROM DIM_VENDOR_CONTRACT_VERSION WHERE "
        ) + " AND ".join(where_clauses) + " ORDER BY effective_date DESC"

        try:
            result = conn.execute(sql).fetchall()
        except Exception as e:
            return f"Error querying contracts: {e}"

        if not result:
            return f"No contract found for vendor {vendor_id}" + (f" on date {effective_date}" if effective_date else "") + "."

        lines = [f"Vendor Contract: {vendor_id}"]
        if effective_date:
            lines.append(f"Effective date: {effective_date}")
        lines.append("")
        for row in result:
            lines.append(f"Version: {row[0]}  (v{row[2]}) | Period: {row[3]} to {row[4] or 'present'}")
            if row[5]:
                lines.append(f"  Amendment: {row[5]}")
        return "\n".join(lines)

    else:
        return "Error: Invalid policy_type. Use 'refund', 'loyalty', 'return', 'vendor_contract', or 'all'."

# TOOLS registry removed — tools are now served via mcp_server.py (FastMCP).
# Agent accesses tools through the MCP client (mcp_client.py) instead.
