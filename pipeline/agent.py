"""
FahMai Agentic Pipeline — LangGraph Agent (MCP Edition)

Implements a ReAct-style tool-calling agent using LangGraph.
Tools are accessed through the MCP client (mcp_client.py) instead of
direct function calls. This decouples the agent from the tool implementation.

Architecture:
    START -> call_model -> [has tool_calls?] -> execute_tools -> call_model
                            | (no)
                           END
"""

from __future__ import annotations

import json
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph
from openai import AsyncOpenAI

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from guardrail import SECURITY_PROMPT_ADDON, sanitize_tool_output
from mcp_client import FahMaiMCPClient


# ---------------------------------------------------------------------------
# MCP client (set once before running questions)
# ---------------------------------------------------------------------------

_mcp_client: FahMaiMCPClient | None = None
_openai_tool_defs: list[dict[str, Any]] = []


async def init_mcp_client(client: FahMaiMCPClient) -> list[dict[str, Any]]:
    global _mcp_client, _openai_tool_defs
    _mcp_client = client
    _openai_tool_defs = await client.to_openai_definitions()
    return _openai_tool_defs


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an enterprise data analyst agent for FahMai (ฟ้าใหม่), a multi-channel electronics retailer in Thailand.
""" + SECURITY_PROMPT_ADDON + """

Your job is to answer user questions about FahMai's business data by:
1. Planning what information you need
2. Calling tools to retrieve data from tables and documents
3. Reasoning about the results
4. Producing a clear, concise answer in Thai or English (match the question's language)

AVAILABLE TOOLS:
- explore_schema — List all tables or inspect a table's columns and sample rows
- query_sql — Run SQL queries against the data warehouse (primary data tool)
- search_documents — Search memos, minutes, emails, policies, product specs. Set return_content=true to read full text.
- search_chats — Search LINE OA (customer) and LINE WORKS (internal) chats. Set return_content=true to read full chat.
- lookup_policy — Look up refund/loyalty/return policies or vendor contracts effective at specific dates

IMPORTANT RULES:
1. ALWAYS explore table schemas before writing SQL — column names are case-sensitive.
2. Use `business_event_date` for time-based filtering. `effective_date` and `as_of_date` are metadata columns often NULL — do not filter on them.
3. All numeric columns are stored as VARCHAR. ALWAYS CAST to DECIMAL before math: `CAST(amount_thb AS DECIMAL)` or `CAST(net_total_thb AS DECIMAL)`.
4. Data covers 2024-01-01 through 2025-12-31. Dates use ISO 8601 (YYYY-MM-DD).
5. Bank transaction types use 'deposit'/'withdrawal'/'transfer'/'fee' — NOT 'credit'/'debit'.
6. For document/chat search, use SHORT keywords (1-2 words max). Try multiple searches with different terms if the first fails.
7. When a question references internal chat threads, memos, or policies, use search_documents or search_chats first.
8. For policy/contract questions (refund windows, loyalty rates, vendor contracts), use lookup_policy.
9. For aggregations, always provide exact numbers, not approximations.
10. If you cannot find the answer after thorough searching, say so honestly.
11. Do NOT make up data — only report what the tools return.
12. Keep final answers concise — 2-3 sentences unless the question asks for a detailed breakdown.
13. After getting tool results, reason about them and decide if you need more data before answering."""


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    messages: list[dict[str, Any]]
    question: str
    iteration_count: int
    total_output_tokens: int


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    return _client


# ---------------------------------------------------------------------------
# Nodes (async — MCP calls are async)
# ---------------------------------------------------------------------------

async def call_model(state: AgentState) -> AgentState:
    """Call the LLM with the conversation history and tool definitions."""
    client = _get_client()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *state["messages"],
    ]

    response = await client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        tools=_openai_tool_defs,
        tool_choice="auto",
    )

    msg = response.choices[0].message
    new_messages = list(state["messages"])

    if msg.tool_calls:
        assistant_msg = {
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ],
        }
        new_messages.append(assistant_msg)
    else:
        new_messages.append({
            "role": "assistant",
            "content": msg.content or "",
        })

    tokens = response.usage.completion_tokens if response.usage else 0
    return {
        "messages": new_messages,
        "question": state["question"],
        "iteration_count": state["iteration_count"] + 1,
        "total_output_tokens": state["total_output_tokens"] + tokens,
    }


async def execute_tools(state: AgentState) -> AgentState:
    """Execute any tool calls from the last assistant message via MCP client."""
    last_msg = state["messages"][-1]
    tool_calls = last_msg.get("tool_calls", [])
    new_messages = list(state["messages"])

    for tc in tool_calls:
        name = tc["function"]["name"]
        try:
            args = json.loads(tc["function"]["arguments"])
        except json.JSONDecodeError:
            args = {}

        try:
            result = await _mcp_client.call_tool(name, args)
        except Exception as e:
            result = f"Tool execution error: {e}"

        safe_result, was_injected = sanitize_tool_output(str(result), source_hint=name)

        new_messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": safe_result,
        })

    return {
        "messages": new_messages,
        "question": state["question"],
        "iteration_count": state["iteration_count"],
        "total_output_tokens": state["total_output_tokens"],
    }


def should_continue(state: AgentState) -> Literal["execute_tools", "__end__"]:
    """Route: if the last message has tool_calls -> execute them, else end."""
    last_msg = state["messages"][-1]
    if last_msg.get("tool_calls"):
        return "execute_tools"
    return "__end__"


def should_loop_or_end(state: AgentState) -> Literal["call_model", "__end__"]:
    """After tool execution, loop back to model (capped at 15 iterations)."""
    if state["iteration_count"] >= 15:
        return "__end__"
    return "call_model"


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------

def build_agent() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("call_model", call_model)
    graph.add_node("execute_tools", execute_tools)

    graph.set_entry_point("call_model")

    graph.add_conditional_edges(
        "call_model",
        should_continue,
        {"execute_tools": "execute_tools", "__end__": END},
    )
    graph.add_conditional_edges(
        "execute_tools",
        should_loop_or_end,
        {"call_model": "call_model", "__end__": END},
    )

    return graph.compile()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def run_question(question: str, verbose: bool = False) -> dict[str, Any]:
    """Run a single question through the agent and return the answer with metadata.

    Requires init_mcp_client() to have been called first.
    """
    agent = get_agent()
    initial_state: AgentState = {
        "messages": [{"role": "user", "content": question}],
        "question": question,
        "iteration_count": 0,
        "total_output_tokens": 0,
    }

    result = await agent.ainvoke(initial_state)

    final_answer = ""
    tool_calls_made = []
    for msg in result["messages"]:
        if msg["role"] == "assistant" and not msg.get("tool_calls"):
            final_answer = msg.get("content", "")
        if msg["role"] == "tool":
            tool_calls_made.append({
                "tool_call_id": msg.get("tool_call_id", ""),
                "content_preview": msg.get("content", "")[:200],
            })

    if verbose:
        for msg in result["messages"]:
            role = msg["role"]
            if role == "tool":
                preview = msg.get("content", "")[:300]
                print(f"  [TOOL RESULT] {preview}...")
            elif role == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    print(f"  [TOOL CALL] {tc['function']['name']}({tc['function']['arguments']})")

    return {
        "question": question,
        "answer": final_answer,
        "iterations": result["iteration_count"],
        "tool_calls": len(tool_calls_made),
        "total_output_tokens": result.get("total_output_tokens", 0),
    }
