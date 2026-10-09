"""
PHASE 12 - Advanced AI-agent feature: a multi-step analysis agent.

For "why" questions ("Why did sales decrease in March?") one query is not enough. The agent
runs a short investigation loop:

    think -> run_sql (validated + executed safely) -> observe -> think -> ... -> final_answer

It uses a plain JSON action protocol, so it works with any provider (no vendor-specific
tool-calling needed). Every query goes through the SAME validator and read-only executor as
normal questions - the agent has no extra privileges.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .llm import BaseLLM, LLMError, complete_json
from .prompts import AGENT_SYSTEM, BUSINESS_NOTES
from .query_executor import QueryExecutionError, execute_query
from .sql_generator import format_history
from .sql_validator import SQLValidator

OBSERVATION_ROWS = 25


@dataclass
class AgentStep:
    thought: str
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    error: str | None = None


@dataclass
class AgentResult:
    answer: str
    steps: list[AgentStep]
    completed: bool


class AnalysisAgent:
    def __init__(self, llm: BaseLLM, validator: SQLValidator, schema_prompt: str, settings: Settings):
        self.llm = llm
        self.validator = validator
        self.settings = settings
        self.system = AGENT_SYSTEM.format(
            max_steps=settings.agent_max_steps, business_notes=BUSINESS_NOTES, schema=schema_prompt
        )

    def run(self, question: str, history: list[dict]) -> AgentResult:
        ctx = format_history(history)
        messages = [{"role": "user", "content": (f"{ctx}\n\n" if ctx else "") + f"Question: {question}"}]
        steps: list[AgentStep] = []

        for _ in range(self.settings.agent_max_steps):
            try:
                action = complete_json(self.llm, self.system, messages, task="agent", max_tokens=1200)
            except LLMError as exc:
                return AgentResult(f"The analysis stopped because the model failed: {exc}", steps, False)

            if action.get("action") == "final_answer":
                return AgentResult(str(action.get("answer", "")).strip(), steps, True)

            thought = str(action.get("thought", ""))
            sql = action.get("sql") if action.get("action") == "run_sql" else None
            step = AgentStep(thought=thought, sql=sql if isinstance(sql, str) else None)
            steps.append(step)
            messages.append({"role": "assistant", "content": json.dumps(action)})

            if not step.sql:
                observation = "Error: reply with a valid action (run_sql or final_answer)."
                step.error = "Invalid action"
            else:
                check = self.validator.validate(step.sql)
                if not check.ok:
                    step.error = "; ".join(check.errors)
                    observation = f"Error: {step.error}"
                else:
                    step.sql = check.sql
                    try:
                        result = execute_query(check.sql)
                        step.columns, step.rows = result.columns, result.rows
                        observation = json.dumps({"columns": result.columns, "rows": result.rows[:OBSERVATION_ROWS],
                                                  "total_rows": result.row_count})
                    except QueryExecutionError as exc:
                        step.error = str(exc)
                        observation = f"Error: {exc}"
            messages.append({"role": "user", "content": f"Observation: {observation}"})

        # step budget used up -> force a conclusion from what we have
        messages.append({"role": "user", "content":
                         "Observation: step limit reached. Reply now with a final_answer based on the evidence so far."})
        try:
            action = complete_json(self.llm, self.system, messages, task="agent", max_tokens=1200)
            if action.get("action") == "final_answer":
                return AgentResult(str(action.get("answer", "")).strip(), steps, True)
        except LLMError:
            pass
        return AgentResult("I ran out of steps before reaching a firm conclusion. See the queries above.", steps, False)
