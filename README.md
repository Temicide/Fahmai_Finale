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
python pipeline/pipeline.py --id L3-Q-EASY-001   # Single question by ID
python pipeline/pipeline.py -q "What is FahMai's total revenue in 2025?"
python pipeline/pipeline.py --output submission.csv  # Export results
```

Requires Python 3.10+. The default model is `gpt-4o-mini` (configurable via `LLM_MODEL` in `.env`).

## What it does

| Component | Description |
|-----------|-------------|
| `pipeline/agent.py` | LangGraph ReAct agent: loops between reasoning and tool calls (up to 15 iterations) |
| `pipeline/tools.py` | Tool layer: SQL queries via DuckDB, document search, chat transcript search |
| `pipeline/pipeline.py` | CLI entry point with demo, batch, and interactive modes |
| `download_data.py` | Downloads the Kaggle competition dataset |

The agent is given 7 tools: `list_tables`, `get_table_schema`, `query_csv`, `search_docs`, `read_doc`, `search_chats`, `read_chat`. It decides which to use based on each question.

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
| `OPENAI_API_KEY` | — | OpenAI API key (required) |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Custom endpoint for proxies/alternative providers |
| `LLM_MODEL` | `gpt-4o-mini` | Model to use for agent reasoning |
| `FAHMAI_DATA_DIR` | `./fah-mai-the-finale-enterprise-data-agentic-showdown` | Override data location |

## Competition

This is an entry for the [FahMai Kaggle competition](https://www.kaggle.com/competitions/fah-mai-the-finale-enterprise-data-agentic-showdown). Questions span four difficulty tiers (EASY, MED, HARD, XHARD) and cover cross-domain reasoning — requiring the agent to reconcile structured data with narrative evidence from memos, chats, and reports.

## License

MIT
