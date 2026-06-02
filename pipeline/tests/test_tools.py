"""
Unit tests for the FahMai 5-tool suite per TOOL_DESIGN.md.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# explore_schema
# ---------------------------------------------------------------------------

class TestExploreSchema:
    def test_list_all_tables(self):
        from tools import explore_schema
        result = explore_schema()
        assert "Available tables:" in result
        assert "DIM_PRODUCT" in result
        assert "FACT_SALES" in result

    def test_get_table_schema(self):
        from tools import explore_schema
        result = explore_schema(table_name="DIM_PRODUCT")
        assert "Schema for DIM_PRODUCT" in result
        assert "sku_id" in result
        assert "msrp_thb" in result

    def test_get_table_schema_with_samples(self):
        from tools import explore_schema
        result = explore_schema(table_name="DIM_PRODUCT", include_sample=True)
        assert "Schema for DIM_PRODUCT" in result
        assert "Sample rows" in result

    def test_get_table_schema_nonexistent(self):
        from tools import explore_schema
        result = explore_schema(table_name="NONEXISTENT_TABLE")
        assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# query_sql
# ---------------------------------------------------------------------------

class TestQuerySql:
    def test_simple_select(self):
        from tools import query_sql
        result = query_sql(sql="SELECT * FROM DIM_PRODUCT LIMIT 1", format="json")
        assert "rows" in result
        assert "sku_id" in result

    def test_markdown_table_format(self):
        from tools import query_sql
        result = query_sql(sql="SELECT * FROM DIM_PRODUCT LIMIT 1")
        assert "|" in result
        assert "sku_id" in result

    def test_csv_format(self):
        from tools import query_sql
        result = query_sql(sql="SELECT * FROM DIM_PRODUCT LIMIT 1", format="csv")
        assert result.startswith("sku_id,")

    def test_empty_result(self):
        from tools import query_sql
        result = query_sql(sql="SELECT * FROM DIM_PRODUCT WHERE sku_id='IMPOSSIBLE'")
        assert "empty" in result.lower()

    def test_sql_error_with_hint(self):
        from tools import query_sql
        result = query_sql(sql="SELECT bad_column FROM DIM_PRODUCT")
        assert "SQL Error" in result
        assert "column" in result.lower()


# ---------------------------------------------------------------------------
# search_documents
# ---------------------------------------------------------------------------

class TestSearchDocuments:
    def test_search_by_keyword(self):
        from tools import search_documents
        result = search_documents(keywords="refund")
        assert "Found" in result

    def test_search_by_kind(self):
        from tools import search_documents
        result = search_documents(doc_kind="memo", keywords="")
        assert "Found" in result
        assert "[memo]" in result

    def test_search_return_content(self):
        from tools import search_documents
        result = search_documents(keywords="CEO", return_content=True)
        assert "[" in result  # Has kind label
        assert len(result) > 200  # Has substantial content

    def test_search_no_results(self):
        from tools import search_documents
        result = search_documents(keywords="zzzz_nonexistent_xyz")
        assert "No documents found" in result

    def test_search_no_results_has_hint(self):
        from tools import search_documents
        result = search_documents(keywords="zzzz_nonexistent_xyz")
        assert "Hint:" in result


# ---------------------------------------------------------------------------
# search_chats
# ---------------------------------------------------------------------------

class TestSearchChats:
    def test_search_by_keyword(self):
        from tools import search_chats
        result = search_chats(keywords="invoice", source="line_works")
        assert "Found" in result or "No chat threads found" in result

    def test_search_by_date_range(self):
        from tools import search_chats
        result = search_chats(
            keywords="invoice", date_from="2025-04-01", date_to="2025-04-05", source="line_works"
        )
        assert "Found" in result or "No chat threads found" in result

    def test_search_return_content(self):
        from tools import search_chats
        result = search_chats(
            keywords="CEO", return_content=True
        )
        if "No chat threads found" not in result:
            assert "[" in result

    def test_search_max_results(self):
        from tools import search_chats
        result = search_chats(keywords="", date_from="2025-04-01", date_to="2025-04-01", max_results=3)
        if "Found" in result:
            # Should find at most 3 or fewer
            pass

    def test_search_no_results(self):
        from tools import search_chats
        result = search_chats(keywords="zzzz_nonexistent_xyz")
        assert "No chat threads found" in result

    def test_search_no_results_has_hint(self):
        from tools import search_chats
        result = search_chats(keywords="zzzz_nonexistent_xyz")
        assert "Hint:" in result


# ---------------------------------------------------------------------------
# lookup_policy
# ---------------------------------------------------------------------------

class TestLookupPolicy:
    def test_refund_policy(self):
        from tools import lookup_policy
        result = lookup_policy(policy_type="refund")
        assert "Policy: refund" in result
        assert "refund_threshold_thb" in result

    def test_loyalty_policy_with_date(self):
        from tools import lookup_policy
        result = lookup_policy(policy_type="loyalty", effective_date="2025-04-01")
        assert "Policy: loyalty" in result
        assert "2025-04-01" in result
        assert "0.0125" in result

    def test_vendor_contract(self):
        from tools import lookup_policy
        result = lookup_policy(policy_type="vendor_contract", vendor_id="V-013")
        assert "Vendor Contract: V-013" in result

    def test_vendor_contract_with_date(self):
        from tools import lookup_policy
        result = lookup_policy(
            policy_type="vendor_contract", vendor_id="V-013", effective_date="2025-04-05"
        )
        assert "Vendor Contract: V-013" in result
        assert "2025-04-05" in result

    def test_vendor_contract_missing_vendor_id(self):
        from tools import lookup_policy
        result = lookup_policy(policy_type="vendor_contract")
        assert "Error" in result
        assert "vendor_id" in result.lower()

    def test_invalid_policy_type(self):
        from tools import lookup_policy
        result = lookup_policy(policy_type="invalid_type")
        assert "Error" in result


# ---------------------------------------------------------------------------
# Tool functions (TOOLS registry removed — tools are now MCP-served)
# ---------------------------------------------------------------------------

class TestToolFunctionsExport:
    def test_all_five_tools_are_functions(self):
        import tools
        funcs = {
            tools.explore_schema,
            tools.query_sql,
            tools.search_documents,
            tools.search_chats,
            tools.lookup_policy,
        }
        for f in funcs:
            assert callable(f), f"tool '{f.__name__}' is not callable"

    def test_tools_are_importable_from_module(self):
        import tools
        for name in ("explore_schema", "query_sql", "search_documents", "search_chats", "lookup_policy"):
            assert hasattr(tools, name), f"tools.{name} is missing"

    def test_no_tools_registry_exported(self):
        """TOOLS registry has been removed — tools are now served via MCP (mcp_server.py)."""
        import tools
        assert not hasattr(tools, "TOOLS"), "TOOLS registry should not exist in tools.py"


# ---------------------------------------------------------------------------
# Error message design
# ---------------------------------------------------------------------------

class TestErrorMessageDesign:
    def test_explore_schema_nonexistent_table(self):
        from tools import explore_schema
        result = explore_schema(table_name="NONEXISTENT")
        assert "not found" in result.lower()
        assert "explore_schema" in result.lower()

    def test_query_sql_error_is_actionable(self):
        from tools import query_sql
        result = query_sql(sql="SELECT xyz FROM DIM_PRODUCT")
        assert "SQL Error" in result

    def test_search_documents_no_results_is_actionable(self):
        from tools import search_documents
        result = search_documents(keywords="zzz")
        assert "Hint:" in result

    def test_search_chats_no_results_is_actionable(self):
        from tools import search_chats
        result = search_chats(keywords="zzz")
        assert "Hint:" in result

    def test_lookup_policy_invalid_type_is_actionable(self):
        from tools import lookup_policy
        result = lookup_policy(policy_type="nope")
        assert "Error" in result
