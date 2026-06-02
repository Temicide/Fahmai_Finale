# Agent Instructions

## Package Manager

Use **pip**: `pip install -r pipeline/requirements.txt`

## Commit Attribution

AI commits MUST include:

```
Co-Authored-By: Claude Sonnet 4 <noreply@anthropic.com>
```

## File-Scoped Commands

| Task          | Command                                  |
| ------------- | ---------------------------------------- |
| Test (all)    | `pytest pipeline/tests/ -v`              |
| Test (single) | `pytest pipeline/tests/test_tools.py -v` |
| Run agent     | `python pipeline/pipeline.py`            |

## Key Conventions

- Python 3.10+. No type checker or linter configured — add per preference.
- Environment variables in `pipeline/.env` (copy from `pipeline/.env.example`)
- Data directory: `fah-mai-the-finale-enterprise-data-agentic-showdown/`
- Agent logic: `pipeline/agent.py` (LangGraph ReAct), tools: `pipeline/tools.py`
- Guardrail: `pipeline/guardrail.py` — regex-based prompt injection defense
- Read `README.md` for full project overview and data bundle structure

## Testing

Single test file at `pipeline/tests/test_tools.py`. Import patterns:

```python
from pipeline.tools import explore_schema, query_sql, search_documents
```
