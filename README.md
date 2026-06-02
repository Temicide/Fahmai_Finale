# FahMai: The Finale — Enterprise Data Agentic Showdown

A state-of-the-art **LangGraph-powered LLM agent** designed to answer complex, cross-domain business queries about **FahMai** (ฟ้าใหม่), a leading multi-channel electronics retailer in Thailand. 

The agent operates like an expert human analyst: it dynamically explores schemas, constructs precise DuckDB SQL queries, searches company emails/memos, scans over **53,000+ customer and internal chat transcripts**, and reconciles accounts. The entire system is decoupled using the **Model Context Protocol (FastMCP)** and secured with a **dual-layer, zero-trust guardrail**.

---

## Architecture Overview

The system decouples reasoning (LangGraph Agent) from capabilities (MCP Tools) using the Model Context Protocol. A dual-layer security guardrail monitors inputs and sanitizes tool outputs before the LLM reads them.

```mermaid
graph TD
    User([User Question]) --> GR_Pre{Guardrail Pre-Gate}
    GR_Pre -- "BLOCKED (Injection)" --> BlockedResponse[Return Safe Default Answer]
    GR_Pre -- "SAFE / CAUTION" --> Agent[LangGraph Agent]
    
    subgraph "LangGraph ReAct Loop"
        Agent --> CallModel[Call LLM Model]
        CallModel --> ModelChoice{Decision}
        ModelChoice -- "Needs Data (Tool Call)" --> Client[MCP Client]
        ModelChoice -- "Final Answer" --> FinalAnswer[Final Output]
    end
    
    subgraph "FastMCP Server & Tools"
        Client --> Server[FastMCP Server]
        Server --> SQL[query_sql / explore_schema]
        Server --> Docs[search_documents / lookup_policy]
        Server --> Chats[search_chats]
    end
    
    SQL & Docs & Chats --> ToolOutputs[Raw Tool Results]
    ToolOutputs --> GR_Post{Guardrail Post-Sanitization}
    GR_Post --> SanitizedOutputs[Sanitized Tool Results]
    SanitizedOutputs --> CallModel
```

---

## Quick Start

### 1. Install Dependencies
Ensure you have Python 3.10+ installed:
```bash
pip install -r pipeline/requirements.txt
```

### 2. Configure Environment Variables
Copy the template and set your LLM credentials:
```bash
cp pipeline/.env.example pipeline/.env
# Edit pipeline/.env and set your OPENAI_API_KEY / LLM_MODEL
```

### 3. Download Competition Data (~2 GB)
Download the operational data warehouse and document bundle:
```bash
python download_data.py
```

### 4. Run the Agent Pipeline
```bash
# Run Demo Mode (runs 6 representative questions covering EASY to XHARD)
python pipeline/pipeline.py

# Run Full Evaluation (runs all 240 questions from questions.csv)
python pipeline/pipeline.py --all

# Run subset by difficulty (EASY, MED, HARD, XHARD)
python pipeline/pipeline.py --subset HARD

# Run a specific question by ID
python pipeline/pipeline.py --id L3-Q-EASY-001

# Run a custom free-form question
python pipeline/pipeline.py -q "What was FahMai's total revenue in 2025?"

# Run with verbose logs showing tool execution and agent reasoning
python pipeline/pipeline.py --verbose

# Export results for submission
python pipeline/pipeline.py --output submission.csv
```

---

## Repository Structure and Components

| Component | Description |
| :--- | :--- |
| [pipeline/agent.py](file:///Users/temicide/Documents/Fahmai_Finale/pipeline/agent.py) | **LangGraph ReAct Agent**: Core loop running up to 15 reasoning-action iterations. |
| [pipeline/tools.py](file:///Users/temicide/Documents/Fahmai_Finale/pipeline/tools.py) | **Tool Implementation**: High-performance DuckDB connections and hybrid text search. |
| [pipeline/mcp_server.py](file:///Users/temicide/Documents/Fahmai_Finale/pipeline/mcp_server.py) | **FastMCP Server**: Exposes tools over stdio and SSE transport protocols (FastMCP). |
| [pipeline/mcp_client.py](file:///Users/temicide/Documents/Fahmai_Finale/pipeline/mcp_client.py) | **MCP Client Adapter**: Asynchronously connects the LangGraph agent to the FastMCP server. |
| [pipeline/guardrail.py](file:///Users/temicide/Documents/Fahmai_Finale/pipeline/guardrail.py) | **Dual-Layer Guardrail**: Anti-injection pre-gate and post-sanitization layer. |
| [pipeline/pipeline.py](file:///Users/temicide/Documents/Fahmai_Finale/pipeline/pipeline.py) | **CLI Entry Point**: Rich terminal interface with progress bars, tables, and incident logs. |
| [pipeline/server.py](file:///Users/temicide/Documents/Fahmai_Finale/pipeline/server.py) | **FastAPI HTTP Server**: Implements endpoints for local and ThaiLLM back-test evaluation tracks. |
| [pipeline/config.py](file:///Users/temicide/Documents/Fahmai_Finale/pipeline/config.py) | **Environment Configuration**: Key bindings, paths, and search constraints. |
| [pipeline/tests/](file:///Users/temicide/Documents/Fahmai_Finale/pipeline/tests) | **Test Suite**: Integration and unit testing framework (43 comprehensive tests). |

---

## Tool Suite Details

The agent is equipped with **5 specialized tools** served over the FastMCP protocol:

1. **`explore_schema(table_name="", include_sample=False)`**
   * List available tables or view column details and sample data.
   * *Rule*: The agent must always explore table schemas before writing SQL.
2. **`query_sql(sql, format="table")`**
   * Run arbitrary SQL queries against the DuckDB warehouse (capped at 200 rows).
   * Supports Markdown Table, CSV, and JSON output formats.
3. **`search_documents(keywords="", date_from="", date_to="", doc_kind="all", return_content=False)`**
   * Pre-indexed hybrid keyword-and-date search across policy memos, all-hands emails, and reports.
4. **`search_chats(keywords="", date_from="", date_to="", source="both", return_content=False)`**
   * High-speed lookup across **53k+ LINE OA and LINE WORKS** chat transcripts.
   * *Strategy*: Pre-filters by filename-date and scans memory strings, avoiding expensive embeddings.
5. **`lookup_policy(policy_type, effective_date="", vendor_id="")`**
   * Dynamic lookup for refund windows, loyalty tier percentages, or active vendor contracts.

---

## Security and Zero-Trust Guardrails

To prevent jailbreaks and malicious prompts within document datasets, the system uses a **dual-layer, high-performance security guardrail** that adds zero LLM overhead for clean queries:

### 1. Direct Prompt Injection Gate (Pre-Gate)
* **Text Normalization**: Strips zero-width characters, maps Cyrillic/Greek homoglyphs to ASCII, collapses whitespace, and standardizes leetspeak to defeat obfuscation.
* **Pattern Matching**: Scans normalized strings with 23 weighted English and Thai regex expressions.
* **Tiered Risk Scoring**:
  * `SAFE` (0.0 - 0.35) -> Pass through.
  * `CAUTION` (0.35 - 0.65) -> Flag in console, pass through.
  * `SUSPICIOUS` (0.65 - 0.85) -> Sends to secondary LLM Judge for a fast safety verdict.
  * `BLOCKED` (0.85 - 1.00) -> Hard block; agent returns a safe default answer.

### 2. Indirect Prompt Injection Sanitizer (Post-Gate)
* Scans all raw tool outputs (e.g., chat transcripts, memos) for instruction-override patterns *before* feeding them to the LLM.
* Redacts malicious instruction spans and replaces them with a `[GUARDRAIL-REDACTED:...]` marker.
* Injects a warning header instructing the LLM to treat the following text strictly as data, not instructions.

---

## Data Warehouse Schema and Conventions

The `fah-mai-the-finale-enterprise-data-agentic-showdown/` directory contains 2 years of enterprise operations (2024-01-01 to 2025-12-31):
* **`tables/`**: 31 dimension and fact tables (CSV files auto-loaded to DuckDB memory).
* **`docs/`**: Internal policy memos, company emails, and 53k+ customer/internal chat transcripts.
* **`reports/`**: Monthly ops and quarterly financial statements.
* **`renders/`**: POS receipts, invoices, bank statements, and warranties.

> [!IMPORTANT]
> **Key Database Rules for SQL Query Construction:**
> 1. **Case-Sensitivity**: Table names and column names match CSV headers exactly and are case-sensitive (e.g., use double quotes like `"FACT_SALES"`).
> 2. **Numeric Representation**: Numeric columns in the CSV tables are stored as strings (`VARCHAR`). You **MUST CAST** them to `DECIMAL` before performing mathematical operations (e.g., `CAST(amount_thb AS DECIMAL)`).
> 3. **Temporal Filtering**: Always filter dates using the `business_event_date` column in ISO 8601 format (`YYYY-MM-DD`). Avoid filtering on `effective_date` or `as_of_date` since they represent metadata and are often null.
> 4. **Bank Transaction Types**: Standardized transaction classes are `'deposit'`, `'withdrawal'`, `'transfer'`, and `'fee'` (do *not* use `'credit'` or `'debit'`).

---

## Testing

The repository includes a comprehensive integration and unit test suite verifying tool outputs, FastMCP structure, and error message actionability.

To run all 43 tests:
```bash
pytest pipeline/tests/ -v
```

---

## License

This project is licensed under the MIT License.

*Last reviewed: 2026-06-02*
