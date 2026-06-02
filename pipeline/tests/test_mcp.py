"""
Structure tests for the FahMai MCP server.

Verifies the MCP server is correctly configured with 5 tools and Pydantic
schemas. Functional correctness of tools is covered by test_tools.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


class TestMcpServerStructure:
    def test_server_created(self):
        import mcp_server
        assert mcp_server.mcp is not None
        assert mcp_server.mcp.name == "fahmai"

    def test_list_tools_returns_5(self):
        import asyncio
        import mcp.types as t
        import mcp_server
        server = mcp_server.mcp._mcp_server
        req = t.ListToolsRequest(method="tools/list")
        handler = server.request_handlers[t.ListToolsRequest]
        result = asyncio.run(handler(req))
        assert len(result.root.tools) == 5

    def test_tool_names(self):
        import asyncio
        import mcp.types as t
        import mcp_server
        server = mcp_server.mcp._mcp_server
        req = t.ListToolsRequest(method="tools/list")
        handler = server.request_handlers[t.ListToolsRequest]
        result = asyncio.run(handler(req))
        names = {tool.name for tool in result.root.tools}
        expected = {"explore_schema", "query_sql", "search_documents", "search_chats", "lookup_policy"}
        assert names == expected

    def test_all_tools_have_input_schemas(self):
        import asyncio
        import mcp.types as t
        import mcp_server
        server = mcp_server.mcp._mcp_server
        req = t.ListToolsRequest(method="tools/list")
        handler = server.request_handlers[t.ListToolsRequest]
        result = asyncio.run(handler(req))
        for tool in result.root.tools:
            assert tool.inputSchema is not None
            assert tool.inputSchema["type"] == "object"
            assert "properties" in tool.inputSchema


class TestMcpPydanticModels:
    def test_explore_schema_model(self):
        from mcp_server import ExploreSchemaInput
        m = ExploreSchemaInput(table_name="DIM_PRODUCT", include_sample=True)
        assert m.model_dump() == {"table_name": "DIM_PRODUCT", "include_sample": True}
        m2 = ExploreSchemaInput()
        assert m2.model_dump() == {"table_name": "", "include_sample": False}

    def test_query_sql_model(self):
        from mcp_server import QuerySqlInput
        m = QuerySqlInput(sql="SELECT 1")
        d = m.model_dump()
        assert d["sql"] == "SELECT 1"
        assert d["format"] == "table"

    def test_search_documents_model(self):
        from mcp_server import SearchDocumentsInput
        m = SearchDocumentsInput(keywords="refund", doc_kind="policy")
        d = m.model_dump()
        assert d["keywords"] == "refund"
        assert d["doc_kind"] == "policy"
        assert d["return_content"] is False

    def test_search_chats_model(self):
        from mcp_server import SearchChatsInput
        m = SearchChatsInput(keywords="invoice", source="line_works")
        d = m.model_dump()
        assert d["keywords"] == "invoice"
        assert d["source"] == "line_works"

    def test_lookup_policy_model(self):
        from mcp_server import LookupPolicyInput
        m = LookupPolicyInput(policy_type="refund", effective_date="2025-01-01")
        d = m.model_dump()
        assert d["policy_type"] == "refund"
        assert d["effective_date"] == "2025-01-01"
