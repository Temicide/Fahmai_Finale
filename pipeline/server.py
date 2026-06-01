#!/usr/bin/env python3
"""
FahMai Agentic AI — Back-test HTTP server

Endpoints (competition format):
  POST /agent/local    — Open-weight track
  POST /agent/thaIllm — ThaiLLM track

Input:  { "question": "...", "id": "..." }   (id is optional)
Output: { "id": "...", "answer": "...", "total_output_token": N }

Usage:
  python server.py            # default port 8000
  PORT=9000 python server.py
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class QuestionRequest(BaseModel):
    question: str
    id: Optional[str] = None


class AgentResponse(BaseModel):
    id: str
    answer: str
    total_output_token: int


# ---------------------------------------------------------------------------
# Lifespan — warm up DuckDB once at startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Loading FahMai data warehouse...")
    from tools import _get_db
    _get_db()
    logger.info("Data warehouse ready. Server accepting requests.")
    yield


app = FastAPI(title="FahMai Agentic AI Back-test", version="1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Core pipeline runner (shared by both tracks)
# ---------------------------------------------------------------------------

def _run_pipeline(req: QuestionRequest) -> AgentResponse:
    from agent import run_question
    from guardrail import assess_query

    qid = req.id or str(uuid.uuid4())
    question = req.question

    # Guardrail assessment
    gr = assess_query(question)

    if gr.risk_level == "caution":
        logger.info("[GUARDRAIL CAUTION] id=%s score=%.2f pattern=%s", qid, gr.risk_score, gr.pattern_label)
    elif gr.risk_level == "suspicious":
        lv = gr.llm_verdict.get("verdict", "?") if gr.llm_verdict else "unavailable"
        logger.info("[GUARDRAIL SUSPICIOUS] id=%s score=%.2f llm=%s", qid, gr.risk_score, lv)

    if not gr.is_safe:
        logger.warning("[GUARDRAIL BLOCKED] id=%s pattern=%s — annotated pass-through", qid, gr.pattern_label)
        question = f"[GUARDRAIL-INJECTION-DETECTED: pattern={gr.pattern_label}]\n\n{question}"

    result = run_question(question)

    return AgentResponse(
        id=qid,
        answer=result["answer"],
        total_output_token=result.get("total_output_tokens", 0),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/agent/local", response_model=AgentResponse)
def agent_local(req: QuestionRequest):
    """Open-weight / local model track."""
    try:
        return _run_pipeline(req)
    except Exception as exc:
        logger.exception("[/agent/local] Unhandled error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/agent/thaIllm", response_model=AgentResponse)
def agent_thaIllm(req: QuestionRequest):
    """ThaiLLM track — same pipeline; switch model via LLM_MODEL env var."""
    try:
        return _run_pipeline(req)
    except Exception as exc:
        logger.exception("[/agent/thaIllm] Unhandled error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
