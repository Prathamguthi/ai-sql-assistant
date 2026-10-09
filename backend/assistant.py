"""
The orchestrator - wires every phase into the architecture from the project brief:

    User -> Intent detection -> SQL generation -> SQL validation -> Database
         -> Query result -> AI explanation (+ chart, follow-up suggestions)

PHASE 8: each session keeps the last few turns so follow-ups ("and for 2025?") work.
"""
from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from threading import Lock
from typing import Any

from .agent import AnalysisAgent
from .charts import requested_chart_type, suggest_chart
from .config import Settings, get_settings
from .database import get_schema_info, schema_to_prompt, get_table_names
from .llm import BaseLLM, LLMError, get_llm
from .query_executor import QueryExecutionError, execute_query, make_dry_run
from .sql_generator import SQLGenerator
from .sql_validator import SQLValidator

MAX_SESSIONS = 200
MAX_STORED_TURNS = 10

OFF_TOPIC_REPLY = ("I'm a data assistant - ask me about the employees, departments, customers, products "
                   "or orders in the database. For example: \"Show me the top 5 departments by average salary\" "
                   "or \"Show sales by month\".")


class SessionStore:
    """In-memory conversation history (swap for Redis/DynamoDB when you scale out)."""

    def __init__(self) -> None:
        self._data: OrderedDict[str, list[dict]] = OrderedDict()
        self._lock = Lock()

    def get(self, session_id: str) -> list[dict]:
        with self._lock:
            return list(self._data.get(session_id, []))

    def add(self, session_id: str, turn: dict) -> None:
        with self._lock:
            turns = self._data.setdefault(session_id, [])
            turns.append(turn)
            del turns[:-MAX_STORED_TURNS]
            self._data.move_to_end(session_id)
            while len(self._data) > MAX_SESSIONS:
                self._data.popitem(last=False)

    def clear(self, session_id: str) -> bool:
        with self._lock:
            return self._data.pop(session_id, None) is not None


class Assistant:
    def __init__(self, settings: Settings | None = None, llm: BaseLLM | None = None):
        self.settings = settings or get_settings()
        self.llm = llm or get_llm()
        self.sessions = SessionStore()
        self.refresh_schema()

    def refresh_schema(self) -> None:
        db = self.settings.db_path
        self.schema = get_schema_info(db)
        self.schema_prompt = schema_to_prompt(self.schema)
        self.validator = SQLValidator(get_table_names(db), self.settings.max_rows, make_dry_run(db))
        self.generator = SQLGenerator(self.llm, self.schema_prompt)
        self.agent = AnalysisAgent(self.llm, self.validator, self.schema_prompt, self.settings)

    # ------------------------------------------------------------------ public API
    def chat(self, question: str, session_id: str | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        session_id = session_id or uuid.uuid4().hex
        question = question.strip()
        history = self.sessions.get(session_id)[-self.settings.history_turns:]

        response: dict[str, Any] = {
            "session_id": session_id, "question": question, "intent": None, "answer": "",
            "sql": None, "query_explanation": None, "assumptions": [], "columns": [], "rows": [],
            "row_count": 0, "truncated": False, "chart": None, "follow_ups": [],
            "warnings": [], "optimizations": [], "steps": [], "error": None,
        }

        if len(question) > self.settings.max_question_chars:
            response["error"] = f"Question too long (max {self.settings.max_question_chars} characters)."
            return self._finish(response, started)

        try:
            intent = self.generator.detect_intent(question, history)
            response["intent"] = intent

            if intent == "off_topic":
                response["answer"] = OFF_TOPIC_REPLY
            elif intent == "schema_info":
                response["answer"] = self._describe_schema()
                response["follow_ups"] = ["Show me the top 5 departments by average salary",
                                          "Show sales by month", "How many employees are in each department?"]
            elif intent == "analysis":
                self._run_analysis(question, history, response)
            else:
                self._run_data_query(question, history, response)
        except LLMError as exc:
            response["error"] = f"The language model is unavailable: {exc}"
        except Exception as exc:                     # never leak a stack trace to the client
            response["error"] = f"Unexpected error: {type(exc).__name__}"

        if not response["error"]:
            self._remember(session_id, response)
        return self._finish(response, started)

    # ------------------------------------------------------------------ branches
    def _run_data_query(self, question: str, history: list[dict], response: dict) -> None:
        prev_sql, error = None, None
        for _ in range(self.settings.max_sql_retries + 1):
            gen = self.generator.generate(question, history, prev_sql, error)
            if gen.sql is None:
                response["answer"] = gen.explanation or "I couldn't answer that from the available data."
                return

            check = self.validator.validate(gen.sql)
            response["warnings"] = check.warnings
            response["optimizations"] = check.hints
            if not check.ok:
                if check.blocked:           # security violation: never retry, never run
                    response["error"] = "That query was blocked by the safety checks: " + " ".join(check.errors)
                    response["sql"] = gen.sql
                    return
                prev_sql, error = gen.sql, "; ".join(check.errors)
                continue

            try:
                result = execute_query(check.sql, self.settings.db_path, self.settings.max_rows,
                                       self.settings.query_timeout_s)
            except QueryExecutionError as exc:
                prev_sql, error = check.sql, str(exc)
                continue

            response.update(sql=check.sql, query_explanation=gen.explanation, assumptions=gen.assumptions,
                            columns=result.columns, rows=result.rows, row_count=result.row_count,
                            truncated=result.truncated)
            response["chart"] = suggest_chart(result.columns, result.rows, requested_chart_type(question))
            explained = self.generator.explain_result(question, check.sql, result.columns, result.rows,
                                                      result.truncated)
            response["answer"] = explained["summary"]
            response["follow_ups"] = explained["follow_ups"]
            if result.truncated:
                response["warnings"].append(f"Showing the first {self.settings.max_rows} rows only.")
            return

        response["error"] = f"I couldn't produce a valid query after several attempts. Last problem: {error}"
        response["sql"] = prev_sql

    def _run_analysis(self, question: str, history: list[dict], response: dict) -> None:
        result = self.agent.run(question, history)
        response["answer"] = result.answer
        response["steps"] = [
            {"thought": s.thought, "sql": s.sql, "columns": s.columns, "rows": s.rows[:50], "error": s.error}
            for s in result.steps
        ]
        first_ok = next((s for s in result.steps if s.rows), None)
        if first_ok:
            response["columns"], response["rows"] = first_ok.columns, first_ok.rows
            response["row_count"] = len(first_ok.rows)
            response["sql"] = first_ok.sql
            response["chart"] = suggest_chart(first_ok.columns, first_ok.rows)
        if not result.completed:
            response["warnings"].append("The analysis did not finish cleanly; treat the answer with care.")

    def _describe_schema(self) -> str:
        lines = ["I can query these tables:"]
        for t in self.schema["tables"]:
            cols = ", ".join(c["name"] for c in t["columns"])
            lines.append(f'- **{t["name"]}** ({t["row_count"]} rows): {cols}')
        return "\n".join(lines)

    # ------------------------------------------------------------------ bookkeeping
    def _remember(self, session_id: str, response: dict) -> None:
        self.sessions.add(session_id, {
            "question": response["question"],
            "sql": response["sql"],
            "columns": response["columns"],
            "row_count": response["row_count"],
            "preview": response["rows"][:5],
        })

    @staticmethod
    def _finish(response: dict, started: float) -> dict:
        response["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return response
