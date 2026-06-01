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
# Tool: list_tables
# ---------------------------------------------------------------------------

def list_tables() -> str:
    """List all available dimension and fact tables with brief descriptions."""
    conn = _get_db()
    tables = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY table_name").fetchall()
    lines = ["Available tables:\n"]
    for (name,) in tables:
        row_count = conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        cols = [c[0] for c in conn.execute(f'DESCRIBE "{name}"').fetchall()]
        prefix = "DIM" if name.startswith("DIM") else "FACT" if name.startswith("FACT") else "  "
        lines.append(f"  [{prefix}] {name}  ({row_count} rows, {len(cols)} cols)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_table_schema
# ---------------------------------------------------------------------------

def get_table_schema(table_name: str) -> str:
    """Get the column schema for a specific table."""
    conn = _get_db()
    try:
        rows = conn.execute(f'DESCRIBE "{table_name}"').fetchall()
        cols = [f"  {r[0]:<40s} {r[1]}" for r in rows]
        return f"Schema for {table_name} ({len(cols)} columns):\n" + "\n".join(cols)
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Tool: query_csv (the primary SQL tool)
# ---------------------------------------------------------------------------

def query_csv(sql: str) -> str:
    """Execute a SQL query against the FahMai data warehouse (DuckDB in-memory).
    All dimension and fact tables are pre-loaded. Use standard SQL syntax.
    Column names are case-sensitive and match CSV headers exactly.
    Results are limited to 200 rows. Use LIMIT and OFFSET if you need more.
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

        # Format as markdown table
        col_widths = [max(len(str(c)), max(len(str(r[i])) for r in rows)) for i, c in enumerate(columns)]
        header = "| " + " | ".join(c.ljust(col_widths[i]) for i, c in enumerate(columns)) + " |"
        sep = "|-" + "-|-".join("-" * col_widths[i] for i in range(len(columns))) + "-|"
        data_lines = ["| " + " | ".join(str(r[i]).ljust(col_widths[i]) for i in range(len(columns))) + " |" for r in rows]

        out = [header, sep] + data_lines
        out.append(f"\n({len(rows)} rows" + (" — truncated, use LIMIT/OFFSET for more" if truncated else "") + ")")
        return "\n".join(out)
    except Exception as e:
        return f"SQL Error: {e}"


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


def search_docs(keywords: str = "", date_from: str = "", date_to: str = "", doc_kind: str = "") -> str:
    """Search structured documents (memos, minutes, emails, policies, product specs, reports).
    
    Parameters:
    - keywords: space-separated search terms (matched case-insensitively in content)
    - date_from / date_to: filter by document date (YYYY-MM-DD format)
    - doc_kind: filter by type — 'memo', 'minutes', 'email', 'policy', 'product_spec', 'report', 'store_info'
    
    Returns matching document list with paths and content previews.
    """
    idx = _build_doc_index()
    results = idx.copy()

    if doc_kind:
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
        return f"No documents found. (Searched: kind={doc_kind or 'all'}, date={date_from or 'any'}→{date_to or 'any'}, keywords='{keywords}')"

    results = results[:MAX_DOC_SEARCH_RESULTS]
    lines = [f"Found {len(results)} documents:"]
    for d in results:
        preview = d["content"][:300].replace("\n", " ").strip()
        lines.append(f"\n  [{d['kind']}] {d['filename']}")
        if d.get("date"):
            lines.append(f"    Date: {d['date']}")
        lines.append(f"    Preview: {preview}...")
    return "\n".join(lines)


def read_doc(file_path: str) -> str:
    """Read the full content of a specific document. Use the path from search_docs results."""
    try:
        path = Path(file_path)
        if not path.exists():
            # Try resolving relative to docs/
            path = DOCS_DIR / file_path
        if not path.exists():
            path = REPORTS_DIR / file_path
        if not path.exists():
            return f"Document not found: {file_path}"
        content = path.read_text(encoding="utf-8")
        return content[:8000]  # Cap at 8k chars to avoid context overflow
    except Exception as e:
        return f"Error reading document: {e}"


# ---------------------------------------------------------------------------
# Chat search (LINE OA + LINE WORKS)
# ---------------------------------------------------------------------------

# Pre-built filename -> date mapping for fast date-range filtering
_chat_file_index: dict[str, list[Path]] | None = None


def _build_chat_index() -> dict[str, list[Path]]:
    """Build a date-keyed index of chat files for fast date-range lookups."""
    global _chat_file_index
    if _chat_file_index is not None:
        return _chat_file_index

    _chat_file_index = {"line_oa": [], "line_works": []}
    
    oa_dir = DOCS_DIR / "chat_line_oa"
    if oa_dir.exists():
        for f in sorted(oa_dir.glob("*.md")):
            _chat_file_index["line_oa"].append(f)

    lw_dir = DOCS_DIR / "chat_line_works"
    if lw_dir.exists():
        for f in sorted(lw_dir.glob("*.md")):
            _chat_file_index["line_works"].append(f)

    return _chat_file_index


def search_chats(
    keywords: str = "",
    date_from: str = "",
    date_to: str = "",
    source: str = "both",
    max_files: int = MAX_CHAT_SEARCH_RESULTS,
) -> str:
    """Search LINE OA (customer chats) and LINE WORKS (internal team chats).

    The search uses a two-phase approach optimized for 53k+ files:
    1. DATE pre-filter: extracts YYYY-MM-DD from filenames (fast, no file read)
    2. KEYWORD grep: reads only date-matching files and searches content

    Parameters:
    - keywords: space-separated terms (all must match)
    - date_from / date_to: YYYY-MM-DD range (uses filename date, fast)
    - source: 'line_oa', 'line_works', or 'both' (default)
    - max_files: maximum number of matching files to return

    Returns file paths with content snippets.
    """
    idx = _build_chat_index()

    sources = []
    if source in ("both", "line_oa"):
        sources.append(("line_oa", idx["line_oa"]))
    if source in ("both", "line_works"):
        sources.append(("line_works", idx["line_works"]))

    kw_list = [k.lower() for k in keywords.split()] if keywords else []
    matches = []

    for src_label, files in sources:
        for fp in files:
            # Phase 1: date filter from filename
            fname = fp.name
            date_in_name = _extract_date_from_filename(fname)
            if date_from and (not date_in_name or date_in_name < date_from):
                continue
            if date_to and (not date_in_name or date_in_name > date_to):
                continue

            if not kw_list:
                matches.append((src_label, fp, "", date_in_name))
                if len(matches) >= max_files:
                    break
                continue

            # Phase 2: keyword search in content (OR logic — any keyword matches)
            try:
                content = fp.read_text(encoding="utf-8")
                content_lower = content.lower()
                if any(kw in content_lower for kw in kw_list):
                    matching_kw = next(kw for kw in kw_list if kw in content_lower)
                    snippet = _extract_snippet(content, matching_kw)
                    matches.append((src_label, fp, snippet, date_in_name))
            except Exception:
                continue

            if len(matches) >= max_files:
                break
        if len(matches) >= max_files:
            break

    if not matches:
        return (
            f"No chat threads found. "
            f"(source={source}, date={date_from or 'any'}→{date_to or 'any'}, keywords='{keywords}')"
        )

    lines = [f"Found {len(matches)} chat threads:\n"]
    for src_label, fp, snippet, date_in_name in matches[:max_files]:
        fname = fp.name
        lines.append(f"  [{src_label}] {fname}")
        if date_in_name:
            lines.append(f"    Date: {date_in_name}")
        if snippet:
            lines.append(f"    Snippet: {snippet[:200]}...")
    return "\n".join(lines)


def read_chat(file_path: str, source: str = "line_oa") -> str:
    """Read a full chat transcript. source should be 'line_oa' or 'line_works'."""
    base = DOCS_DIR / f"chat_{source}"
    path = base / file_path
    if not path.exists():
        path = Path(file_path)
    if not path.exists():
        return f"Chat file not found: {file_path}"

    try:
        content = path.read_text(encoding="utf-8")
        return content[:10000]  # Cap at 10k chars
    except Exception as e:
        return f"Error reading chat: {e}"


def _extract_snippet(text: str, keyword: str, context_chars: int = 200) -> str:
    """Extract a snippet around the first occurrence of a keyword."""
    idx = text.lower().find(keyword.lower())
    if idx < 0:
        return text[:context_chars]
    start = max(0, idx - context_chars // 2)
    end = min(len(text), idx + context_chars // 2)
    return text[start:end]


# ---------------------------------------------------------------------------
# Tool registry (exported for the agent)
# ---------------------------------------------------------------------------

# Map of tool_name -> (function, description, parameter_schema)
TOOLS = {
    "list_tables": {
        "fn": list_tables,
        "description": "List all available dimension and fact tables with row counts and column counts.",
        "parameters": {},
    },
    "get_table_schema": {
        "fn": get_table_schema,
        "description": "Get column names and types for a specific table. Use before writing queries.",
        "parameters": {
            "table_name": {"type": "string", "description": "Name of the table (e.g., 'DIM_PRODUCT', 'FACT_SALES')"}
        },
    },
    "query_csv": {
        "fn": query_csv,
        "description": (
            "Execute a SQL query against the FahMai data warehouse. "
            "All tables are pre-loaded. Use standard DuckDB SQL. "
            "Column names are case-sensitive. Always use this tool for any data lookup or aggregation."
        ),
        "parameters": {
            "sql": {"type": "string", "description": "A valid DuckDB SQL SELECT query. Use single quotes for strings."}
        },
    },
    "search_docs": {
        "fn": search_docs,
        "description": (
            "Search structured documents: memos, meeting minutes, company emails, "
            "policies, product specs, and reports. Use keyword + date + type filters."
        ),
        "parameters": {
            "keywords": {"type": "string", "description": "Space-separated search terms (case-insensitive). Leave empty to match all."},
            "date_from": {"type": "string", "description": "Filter documents from this date (YYYY-MM-DD). Optional."},
            "date_to": {"type": "string", "description": "Filter documents up to this date (YYYY-MM-DD). Optional."},
            "doc_kind": {"type": "string", "description": "Document type: 'memo', 'minutes', 'email', 'policy', 'product_spec', 'report'. Optional."},
        },
    },
    "read_doc": {
        "fn": read_doc,
        "description": "Read the full content of a document found via search_docs.",
        "parameters": {
            "file_path": {"type": "string", "description": "Path to the document file."}
        },
    },
    "search_chats": {
        "fn": search_chats,
        "description": (
            "Search LINE OA (customer) and LINE WORKS (internal team) chat transcripts. "
            "Uses date pre-filtering from filenames for speed, then keyword search in content."
        ),
        "parameters": {
            "keywords": {"type": "string", "description": "Space-separated search terms."},
            "date_from": {"type": "string", "description": "Start date YYYY-MM-DD. Optional."},
            "date_to": {"type": "string", "description": "End date YYYY-MM-DD. Optional."},
            "source": {"type": "string", "description": "'line_oa', 'line_works', or 'both' (default)."},
        },
    },
    "read_chat": {
        "fn": read_chat,
        "description": "Read a full chat transcript file found via search_chats.",
        "parameters": {
            "file_path": {"type": "string", "description": "Filename of the chat transcript."},
            "source": {"type": "string", "description": "'line_oa' or 'line_works'."},
        },
    },
}
