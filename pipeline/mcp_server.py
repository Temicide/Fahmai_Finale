"""
FahMai MCP Server — FastMCP server wrapping all 5 agent tools.

Provides explore_schema, query_sql, search_documents, search_chats,
and lookup_policy as MCP tools over stdio transport.

Usage:
    python mcp_server.py              # stdio transport (default, for agent)
    python mcp_server.py --sse        # SSE transport (for MCP Inspector)
    python mcp_server.py --sse --port 8765
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

import tools
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

mcp = FastMCP("fahmai")


# ---------------------------------------------------------------------------
# Pydantic input models
# ---------------------------------------------------------------------------

class ExploreSchemaInput(BaseModel):
    table_name: str = Field(
        default="",
        description="Optional. Table name for detailed schema (e.g., 'DIM_PRODUCT', 'FACT_SALES'). Omit to list all tables.",
    )
    include_sample: bool = Field(
        default=False,
        description="Include 3 sample rows (single table mode only).",
    )


class QuerySqlInput(BaseModel):
    sql: str = Field(
        description="A valid DuckDB SQL SELECT query. Use single quotes for strings. Always CAST numeric columns (stored as VARCHAR) to DECIMAL before math.",
    )
    format: str = Field(
        default="table",
        description="Output format: 'table' (markdown, default), 'csv', or 'json'.",
    )


class SearchDocumentsInput(BaseModel):
    keywords: str = Field(
        default="",
        description="Space-separated search terms (case-insensitive, OR logic). Leave empty to match all.",
    )
    date_from: str = Field(
        default="",
        description="Filter documents from this date (YYYY-MM-DD). Optional.",
    )
    date_to: str = Field(
        default="",
        description="Filter documents up to this date (YYYY-MM-DD). Optional.",
    )
    doc_kind: str = Field(
        default="all",
        description="Document type filter: 'memo', 'minutes', 'email', 'policy', 'product_spec', 'report', 'store_info', or 'all'.",
    )
    return_content: bool = Field(
        default=False,
        description="Return full content of first match (true) or list with previews (false).",
    )


class SearchChatsInput(BaseModel):
    keywords: str = Field(
        default="",
        description="Space-separated search terms (OR logic — any keyword match).",
    )
    date_from: str = Field(
        default="",
        description="Start date (YYYY-MM-DD). Optional. Fast pre-filter using filename date.",
    )
    date_to: str = Field(
        default="",
        description="End date (YYYY-MM-DD). Optional. Fast pre-filter using filename date.",
    )
    source: str = Field(
        default="both",
        description="Chat source: 'line_oa' (customer), 'line_works' (internal team), or 'both' (default).",
    )
    return_content: bool = Field(
        default=False,
        description="Return full content of first matching chat (true) or list with snippets (false).",
    )
    max_results: int = Field(
        default=MAX_CHAT_SEARCH_RESULTS,
        description="Maximum number of matching files to return when return_content=false.",
    )


class LookupPolicyInput(BaseModel):
    policy_type: str = Field(
        description="Type of policy or contract: 'refund', 'loyalty', 'return', 'vendor_contract', or 'all'.",
    )
    effective_date: str = Field(
        default="",
        description="Date to check policy/contract effectiveness (YYYY-MM-DD). If omitted, returns all versions.",
    )
    vendor_id: str = Field(
        default="",
        description="For vendor_contract type, specify vendor ID (e.g., 'V-013'). Optional for other types.",
    )


# ---------------------------------------------------------------------------
# MCP tool wrappers
# ---------------------------------------------------------------------------

@mcp.tool(
    name="explore_schema",
    description="Explore the database schema. Without parameters, lists all tables with row/column counts. With table_name, shows detailed column schema and optional sample rows. Use this tool before writing SQL queries to understand available data.",
)
def mcp_explore_schema(params: ExploreSchemaInput) -> str:
    return tools.explore_schema(**params.model_dump())


@mcp.tool(
    name="query_sql",
    description="Execute a SQL query against the FahMai data warehouse (DuckDB). All dimension and fact tables are pre-loaded. Use standard SQL syntax. Column names are case-sensitive and match CSV headers exactly. Results limited to 200 rows by default. Use LIMIT/OFFSET for more. This is the primary tool for any data lookup, aggregation, or analysis.",
)
def mcp_query_sql(params: QuerySqlInput) -> str:
    return tools.query_sql(**params.model_dump())


@mcp.tool(
    name="search_documents",
    description="Search structured documents: memos, meeting minutes, emails, policies, product specs, and reports. Use for finding company policies, internal communications, or documentation. Supports keyword search, date filtering, and document type filtering. Set return_content=true to get full document text.",
)
def mcp_search_documents(params: SearchDocumentsInput) -> str:
    return tools.search_documents(**params.model_dump())


@mcp.tool(
    name="search_chats",
    description="Search LINE OA (customer) and LINE WORKS (internal team) chat transcripts. Uses date pre-filtering from filenames for speed, then keyword search. Use for finding internal discussions, incident reports, customer feedback, or explanations for data anomalies. Set return_content=true to get full chat text.",
)
def mcp_search_chats(params: SearchChatsInput) -> str:
    return tools.search_chats(**params.model_dump())


@mcp.tool(
    name="lookup_policy",
    description="Look up company policies, contract versions, and reference data that change over time. Use for questions about refund policies, loyalty program rates, return windows, or vendor contracts effective at specific dates. Handles date-range logic automatically.",
)
def mcp_lookup_policy(params: LookupPolicyInput) -> str:
    return tools.lookup_policy(**params.model_dump())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="FahMai MCP Server")
    parser.add_argument("--sse", action="store_true", help="Use SSE transport instead of stdio")
    parser.add_argument("--port", type=int, default=8765, help="Port for SSE transport (default: 8765)")
    args = parser.parse_args()

    # Warm up DuckDB + doc/chat indices on import
    tools._get_db()
    tools._build_doc_index()
    tools._build_chat_index()

    if args.sse:
        mcp.run(transport="sse", port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
