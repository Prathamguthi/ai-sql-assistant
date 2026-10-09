"""
PHASE 3 (+8) - Intent detection, LLM -> SQL generation, and result explanation.

The generator never executes anything. It only produces structured output that the
validator and executor then check and run.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .llm import BaseLLM, LLMError, complete_json
from .prompts import (BUSINESS_NOTES, EXPLAIN_SYSTEM, INTENT_SYSTEM, SQL_SYSTEM)

INTENTS = {"data_query", "follow_up", "analysis", "schema_info", "off_topic"}
MAX_RESULT_ROWS_FOR_LLM = 30


@dataclass
class GeneratedSQL:
    sql: str | None
    explanation: str = ""
    assumptions: list[str] = field(default_factory=list)


def format_history(history: list[dict]) -> str:
    """Render previous turns so the model can resolve follow-ups ("and for 2025?")."""
    if not history:
        return ""
    lines = ["Conversation so far (oldest first):"]
    for turn in history:
        lines.append(f'- User asked: {turn["question"]}')
        if turn.get("sql"):
            lines.append(f'  SQL used: {turn["sql"]}')
        if turn.get("columns"):
            lines.append(f'  Result columns: {", ".join(turn["columns"])} ({turn.get("row_count", 0)} rows)')
        if turn.get("preview"):
            lines.append(f'  First rows: {json.dumps(turn["preview"])}')
    return "\n".join(lines)


class SQLGenerator:
    def __init__(self, llm: BaseLLM, schema_prompt: str):
        self.llm = llm
        self.schema_prompt = schema_prompt
        self.sql_system = SQL_SYSTEM.format(business_notes=BUSINESS_NOTES, schema=schema_prompt)

    # ---- step 1: intent -------------------------------------------------------------
    def detect_intent(self, question: str, history: list[dict]) -> str:
        context = format_history(history[-1:])  # only the last turn matters for intent
        content = (f"{context}\n\n" if context else "") + f"Question: {question}"
        try:
            data = complete_json(self.llm, INTENT_SYSTEM, [{"role": "user", "content": content}],
                                 task="intent", max_tokens=100)
        except LLMError:
            return "data_query"        # safest default: try to answer from data
        intent = str(data.get("intent", "data_query"))
        return intent if intent in INTENTS else "data_query"

    # ---- step 2: SQL generation -----------------------------------------------------
    def generate(
        self,
        question: str,
        history: list[dict],
        previous_sql: str | None = None,
        error: str | None = None,
    ) -> GeneratedSQL:
        parts = []
        ctx = format_history(history)
        if ctx:
            parts.append(ctx)
        parts.append(f"Question: {question}")
        if previous_sql and error:
            parts.append(
                "Your previous attempt failed.\n"
                f"SQL: {previous_sql}\nProblem: {error}\n"
                "Return a corrected JSON object."
            )
        data = complete_json(self.llm, self.sql_system,
                             [{"role": "user", "content": "\n\n".join(parts)}], task="sql")
        sql = data.get("sql")
        sql = sql.strip() if isinstance(sql, str) and sql.strip() else None
        assumptions = data.get("assumptions") or []
        return GeneratedSQL(
            sql=sql,
            explanation=str(data.get("explanation", "")),
            assumptions=[str(a) for a in assumptions if a] if isinstance(assumptions, list) else [],
        )

    # ---- step 3: explanation of the results ----------------------------------------
    def explain_result(self, question: str, sql: str, columns: list[str],
                       rows: list[list[Any]], truncated: bool) -> dict[str, Any]:
        shown = rows[:MAX_RESULT_ROWS_FOR_LLM]
        content = (
            f"Question: {question}\nSQL: {sql}\nColumns: {columns}\n"
            f"Rows ({len(shown)} of {len(rows)} shown{', result was truncated' if truncated else ''}): "
            f"{json.dumps(shown)}"
        )
        try:
            data = complete_json(self.llm, EXPLAIN_SYSTEM, [{"role": "user", "content": content}],
                                 task="explain", max_tokens=600)
        except LLMError:
            return {"summary": f"The query returned {len(rows)} row(s).", "follow_ups": []}
        follow_ups = data.get("follow_ups") or []
        return {
            "summary": str(data.get("summary", "")).strip() or f"The query returned {len(rows)} row(s).",
            "follow_ups": [str(f) for f in follow_ups][:3] if isinstance(follow_ups, list) else [],
        }
