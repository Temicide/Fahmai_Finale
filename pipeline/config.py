import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = Path(os.getenv("FAHMAI_DATA_DIR", BASE_DIR / "fah-mai-the-finale-enterprise-data-agentic-showdown"))
TABLES_DIR = DATA_DIR / "tables"
DOCS_DIR = DATA_DIR / "docs"
LOGS_DIR = DATA_DIR / "logs"
REPORTS_DIR = DATA_DIR / "reports"

LLM_API_KEY = os.getenv("OPENAI_API_KEY", os.getenv("WAFER_API_KEY", ""))
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL", os.getenv("WAFER_BASE_URL", "https://api.openai.com/v1"))
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

MAX_CHAT_SEARCH_RESULTS = 20
MAX_DOC_SEARCH_RESULTS = 10
SQL_RESULT_ROW_LIMIT = 200
