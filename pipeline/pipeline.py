#!/usr/bin/env python3
"""
FahMai Agentic AI Pipeline — Entry Point

Tool-calling agent that answers enterprise data questions about FahMai's
operations by querying structured tables (SQL via DuckDB) and unstructured
documents (memos, chats, policies) through a LangGraph ReAct agent.

Usage:
    python pipeline.py                          # Run demo questions
    python pipeline.py --question "Q text"      # Ask a single question
    python pipeline.py --all                    # Run all questions from CSV
    python pipeline.py --subset EASY            # Run only EASY questions
    python pipeline.py --id L3-Q-EASY-001       # Run a specific question by ID

Requirements: pip install -r requirements.txt
Env: Copy .env.example to .env and set WAFER_API_KEY
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table as RichTable

from config import DATA_DIR
from tools import _get_db  # Force DuckDB init

console = Console()

# ---------------------------------------------------------------------------
# Demo questions — diverse selection covering all difficulty levels
# ---------------------------------------------------------------------------

DEMO_QUESTIONS = [
    # EASY: Simple lookup tests
    ("L3-Q-EASY-001", "MSRP ของสินค้ารหัส NT-LT-001 (NovaTech laptop) เป็นเท่าไหร่ครับ"),
    # EASY: Aggregation with group by
    ("L3-Q-EASY-006", "ในปี 2024-2025 สาขาไหนของฟ้าใหม่ที่มีจำนวน transaction การขายมากที่สุด และยอดรายได้รวม (net_total_thb) ของสาขานั้นเป็นเท่าไหร่ครับ"),
    # MED: Complex aggregation & cross-table
    ("L3-Q-MED-002", "ใน FACT_BANK_TRANSACTION ของฟ้าใหม่ ขอ single largest deposit แบบรายแถวเดียว (single transaction, ฝั่ง credit) ตลอดช่วงข้อมูลที่มี ขอครบ 4 อย่างคือ (1) จำนวนเงิน amount_thb (บาท), (2) วันที่ business_event_date, (3) account_id ของบัญชีที่รับเงิน, และ (4) source event ของรายการนี้ (เกี่ยวกับสินค้าตัวไหน / promo campaign อะไร / event ของบริษัทคืออะไร) ครับ"),
    # HARD: Cross-references chat data + structured tables
    ("L3-Q-HARD-001", "วันที่ 2025-04-05 ใน LINE WORKS thread ของทีม Finance / Ops มีการแจ้งเคส invoice ID ซ้ำหลัง schema cutover ของ vendor V-013 (PayWise) ขอให้ตอบ 3 ข้อนี้ครับ: (1) invoice ID ที่ถูกแจ้งว่าซ้ำคือเลขอะไร (2) ใน FACT_VENDOR_PAYMENT มี payment record กี่แถวที่ใช้ invoice ID เดียวกันนี้ และ (3) แต่ละแถวจ่ายเป็นจำนวนเงินเท่าไหร่ (THB) และ posting_date เป็นวันที่เท่าไหร่"),
    # HARD: Phantom dedup + revenue reconciliation
    ("L3-Q-HARD-002", "ในวันที่ 2025-07-15 (เปิดตัวโปรโมชัน SF-LAUNCH-2568) FACT_PROMO_REDEMPTION มีรายการที่ดู phantom / log ซ้ำจาก app channel กี่รายการ และมีการแจ้งปัญหานี้ใน LINE WORKS thread ของวันนั้นด้วยไหมครับ ถ้ามียอด discount ที่ถูกนับซ้ำคิดเป็นเท่าไหร่ (THB) และส่งผลให้ยอด redemption รวมของวันนั้นถูก inflate ไปกี่เปอร์เซ็นต์เมื่อเทียบกับยอดที่ dedup แล้ว"),
    # XHARD: Full reconciliation across tables + docs + bank
    ("L3-Q-XHARD-001", "ขอช่วยตรวจ ROI ของแคมเปญ SF-LAUNCH-2568 (โปรโมชั่นเปิดตัว SF-Galaxy-Pro-2568 ช่วง 2025-07-15 ถึง 2025-07-31) ให้หน่อยครับ ตามที่ทีม Finance สงสัย: app-side redemption log ดูเหมือนจะมี double-logging อยู่บางรายการ เลยอยากให้ช่วยรายงานเป็น 5 ตัวเลขดังนี้ (1) จำนวน redemption ทั้งหมดที่บันทึกใน FACT_PROMO_REDEMPTION ภายใต้ campaign นี้, (2) จำนวน redemption ที่เป็น duplicate (phantom มี txn_id เดียวกันบันทึกหลายครั้งภายใต้ channel ต่างกัน), (3) จำนวน redemption ที่ unique จริงหลังหัก phantom, (4) net discount cost (THB) ที่ตรงกับ POS truth คือ FACT_SALES.discount_total_thb ของ cohort ลูกค้าที่ redeem, และ (5) net revenue (THB) ของ cohort ตาม FACT_SALES.net_total_thb แล้ว ROI = revenue / cost ออกมาเป็นกี่เท่า รบกวนชี้ด้วยว่า reconciliation กับ FACT_BANK_TRANSACTION ของ vendor PayWise (V-013) ในเดือน 2025-07 ยืนยันได้ไหมว่า phantom redemption ไม่มี cash flow ออกจริง"),
]


def load_questions_csv(path: str | Path = "questions.csv") -> list[tuple[str, str]]:
    """Load all questions from the CSV file."""
    questions = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            qid = row.get("id", "").strip()
            question = row.get("question", "").strip()
            if qid and question:
                questions.append((qid, question))
    return questions


def filter_questions(questions: list[tuple[str, str]], subset: str = "all") -> list[tuple[str, str]]:
    """Filter questions by difficulty or ID."""
    if subset.upper() in ("EASY", "MEDIUM", "MED", "HARD", "XHARD"):
        # Match by ID suffix
        return [(qid, q) for qid, q in questions if f"-Q-{subset.upper()}-" in qid or f"-Q-{subset[:3].upper()}-" in qid]
    return questions


def find_question_by_id(questions: list[tuple[str, str]], qid: str) -> tuple[str, str] | None:
    """Find a specific question by ID."""
    for q in questions:
        if q[0] == qid:
            return q
    return None


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="FahMai Agentic AI Pipeline")
    parser.add_argument("--question", "-q", type=str, help="Ask a single free-form question")
    parser.add_argument("--id", type=str, help="Run a specific question by ID (e.g., L3-Q-EASY-001)")
    parser.add_argument("--all", action="store_true", help="Run all questions from questions.csv")
    parser.add_argument("--subset", type=str, help="Filter: EASY, MED, HARD, XHARD")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show tool calls")
    parser.add_argument("--output", "-o", type=str, help="Output CSV file for submissions")
    args = parser.parse_args()

    # --- Initialize DuckDB (warm up) ---
    console.print("[bold cyan]Loading FahMai data warehouse...[/]")
    t0 = time.time()
    _get_db()
    console.print(f"[dim]Tables loaded in {time.time() - t0:.1f}s[/]\n")

    # Lazy-import agent (depends on DuckDB being ready)
    from agent import run_question

    # --- Resolve questions to run ---
    to_run = []

    if args.question:
        to_run.append(("CUSTOM", args.question))
    elif args.id:
        all_qs = load_questions_csv()
        found = find_question_by_id(all_qs, args.id)
        if found:
            to_run.append(found)
        else:
            console.print(f"[red]Question ID {args.id} not found[/]")
            sys.exit(1)
    elif args.all:
        all_qs = load_questions_csv()
        to_run = filter_questions(all_qs, args.subset or "all")
        console.print(f"[bold]Running {len(to_run)} questions[/]\n")
    elif args.subset:
        all_qs = load_questions_csv()
        to_run = filter_questions(all_qs, args.subset)
        console.print(f"[bold]Running {len(to_run)} {args.subset.upper()} questions[/]\n")
    else:
        # Default: demo mode
        to_run = DEMO_QUESTIONS
        console.print("[bold green]Demo Mode — 6 representative questions[/]\n")

    # --- Run ---
    results = []
    for qid, question in to_run:
        console.print(Panel(question, title=f"[bold yellow]{qid}[/]", border_style="blue"))

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=False) as progress:
            task = progress.add_task("Agent reasoning...", total=None)
            t_start = time.time()
            result = run_question(question, verbose=args.verbose)
            elapsed = time.time() - t_start
            progress.stop()

        answer = result["answer"]
        console.print(f"[bold green]Answer:[/] {answer}")
        console.print(f"[dim]({result['iterations']} iterations, {result['tool_calls']} tool calls, {elapsed:.1f}s)[/]\n")

        results.append({
            "id": qid,
            "response": answer,
            "iterations": result["iterations"],
            "tool_calls": result["tool_calls"],
            "time": elapsed,
        })

    # --- Summary ---
    if len(results) > 1:
        table = RichTable(title="Summary")
        table.add_column("ID", style="cyan")
        table.add_column("Answer Preview", style="green", max_width=80)
        table.add_column("Tools", style="magenta")
        table.add_column("Time", style="dim")
        for r in results:
            table.add_row(r["id"], r["response"][:80], str(r["tool_calls"]), f"{r['time']:.1f}s")
        console.print(table)

    # --- Output CSV for submission ---
    if args.output:
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["id", "response"])
            writer.writeheader()
            for r in results:
                writer.writerow({"id": r["id"], "response": r["response"]})
        console.print(f"[bold green]Results written to {args.output}[/]")


if __name__ == "__main__":
    main()
