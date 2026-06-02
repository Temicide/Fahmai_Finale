# FahMai: The Finale — Enterprise Data Agentic Showdown

A LangGraph-powered LLM agent that answers complex business questions about **FahMai** (ฟ้าใหม่), a multi-channel electronics retailer in Thailand. The agent reasons across structured tables (SQL), policy memos, meeting minutes, internal emails, and 53k+ chat transcripts — just like a human analyst would.

## Quick start

```bash
# 1. Install dependencies
pip install -r pipeline/requirements.txt

# 2. Configure your LLM
cp pipeline/.env.example pipeline/.env
# Edit .env — set OPENAI_API_KEY

# 3. Download the competition data (~2 GB)
python download_data.py

# 4. Run the agent
python pipeline/pipeline.py              # Demo mode: 6 representative questions
python pipeline/pipeline.py --all        # Full evaluation (240 questions)
python pipeline/pipeline.py --subset HARD   # Filter by difficulty (EASY/MED/HARD/XHARD)
python pipeline/pipeline.py --id L3-Q-EASY-001   # Single question by ID
python pipeline/pipeline.py -q "What is FahMai's total revenue in 2025?"
python pipeline/pipeline.py --verbose    # Show tool calls and intermediate reasoning
python pipeline/pipeline.py --output submission.csv  # Export results
```

Requires Python 3.10+. The default model is `gpt-4o-mini` (configurable via `LLM_MODEL` in `.env`).

## What it does

| Component | Description |
|-----------|-------------|
| `pipeline/agent.py` | LangGraph ReAct agent: loops between reasoning and tool calls (up to 15 iterations) |
| `pipeline/tools.py` | Tool layer: 5 tools (SQL, doc search, chat search, policy lookup) |
| `pipeline/pipeline.py` | Rich CLI entry point with demo, batch, and interactive modes |
| `pipeline/guardrail.py` | Dual-layer prompt injection defense (regex + LLM judge) |
| `pipeline/server.py` | FastAPI HTTP server — `/agent/local` and `/agent/thaIllm` competition back-test endpoints |
| `pipeline/mcp_server.py` | FastMCP server wrapping all 5 tools over stdio/SSE transport |
| `pipeline/mcp_client.py` | Async MCP client adapter for agent → MCP discovery and invocation |
| `pipeline/config.py` | Environment configuration and path resolution |
| `pipeline/tests/` | Integration tests for all 5 tools + MCP structure tests |
| `download_data.py` | Downloads the Kaggle competition dataset |
| `questions.csv` | 240 competition questions across 4 difficulty tiers |
| `sample_submission.csv` | Expected submission format |

The agent is given 5 tools: `explore_schema`, `query_sql`, `search_documents`, `search_chats`, `lookup_policy`. It decides which to use based on each question.

## Security (Guardrail)

A dual-layer prompt injection defense runs on every question, with tiered risk scoring:

| Layer | What it does |
|-------|-------------|
| **Direct injection gate** | Text normalization (NFKC, homoglyphs, leetspeak, zero-width stripping) → 23 weighted regex patterns (EN + TH) → tiered scoring: **SAFE / CAUTION / SUSPICIOUS / BLOCKED**. SUSPICIOUS-tier queries get a second-pass LLM judge. BLOCKED questions get a safe default answer. |
| **Indirect injection sanitization** | 16 regex patterns scan every tool result before the LLM reads it. Injected spans (e.g. `[SYSTEM] Override...` hidden in chat transcripts) are redacted and replaced with `[GUARDRAIL-REDACTED:...]` markers. A warning header tells the LLM to treat the remaining content as data, not directives. |

Detection is primarily regex-based — zero extra LLM cost for most queries. An incident log is printed at the end of each run.

## Tools

| Tool | Purpose |
|------|---------|
| `explore_schema` | List all tables or inspect a table's columns and sample rows |
| `query_sql` | Primary data tool — run SQL against the DuckDB warehouse (200-row limit) |
| `search_documents` | Search memos, minutes, emails, policies, product specs, and reports with keyword + date filtering |
| `search_chats` | Search 53k+ LINE OA (customer) and LINE WORKS (internal) chat transcripts with date-range pre-filtering |
| `lookup_policy` | Look up refund/loyalty/return policies or vendor contracts effective at specific dates |

## Data bundle

The `fah-mai-the-finale-enterprise-data-agentic-showdown/` directory contains 2 years of enterprise operations data (2024-01-01 → 2025-12-31):

| Folder | Contents |
|--------|----------|
| `tables/` | 31 dimension and fact CSVs (products, sales, customers, vendors, etc.) |
| `docs/memo/` | Internal policy memos |
| `docs/email/` | All-staff company emails |
| `docs/chat_line_oa/` | 37k+ customer LINE OA chat transcripts |
| `docs/chat_line_works/` | 15k+ internal LINE WORKS team threads |
| `reports/` | Monthly operations and quarterly financial reports |
| `logs/` | POS, web orders, WMS, helpdesk, and payment logs |
| `renders/` | Bank statements, receipts, invoices, warranty forms |

See [the data bundle README](fah-mai-the-finale-enterprise-data-agentic-showdown/README.md) for schema details and known data-quality artifacts.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | OpenAI API key (required; `WAFER_API_KEY` also accepted) |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Custom endpoint for proxies/alternative providers |
| `LLM_MODEL` | `gpt-4o-mini` | Model to use for agent reasoning |
| `FAHMAI_DATA_DIR` | `./fah-mai-the-finale-enterprise-data-agentic-showdown` | Override data location |

## Competition

This is an entry for the [FahMai Kaggle competition](https://www.kaggle.com/competitions/fah-mai-the-finale-enterprise-data-agentic-showdown). Questions span four difficulty tiers (EASY, MED, HARD, XHARD) and cover cross-domain reasoning — requiring the agent to reconcile structured data with narrative evidence from memos, chats, and reports.

## Running tests

```bash
pytest pipeline/tests/ -v
```

*Last reviewed: 2026-06-02*

## License

MIT
