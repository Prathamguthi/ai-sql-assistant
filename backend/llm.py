"""
PHASE 3 - LLM layer.

One tiny interface (`complete`) with three providers:

    anthropic  - Claude   (needs ANTHROPIC_API_KEY)
    openai     - OpenAI   (needs OPENAI_API_KEY)
    mock       - offline, rule-based; lets you run and test the whole app with no API key

Select with LLM_PROVIDER, or leave it unset to auto-detect from the available keys.
Override the model with LLM_MODEL.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


class LLMError(Exception):
    """The LLM call failed or returned something unusable."""


class BaseLLM:
    name = "base"

    def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        task: str = "generic",
        max_tokens: int = 1500,
    ) -> str:
        raise NotImplementedError


# --------------------------------------------------------------------------- Anthropic
class AnthropicLLM(BaseLLM):
    name = "anthropic"

    def __init__(self, model: str | None = None):
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMError("Install the SDK: pip install anthropic") from exc
        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_ANTHROPIC_MODEL
        self.client = Anthropic()  # reads ANTHROPIC_API_KEY

    def complete(self, system, messages, *, task="generic", max_tokens=1500) -> str:
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=max_tokens, system=system, messages=messages
            )
        except Exception as exc:  # network, auth, rate limit ...
            raise LLMError(f"Anthropic API error: {exc}") from exc
        return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")


# --------------------------------------------------------------------------- OpenAI
class OpenAILLM(BaseLLM):
    name = "openai"

    def __init__(self, model: str | None = None):
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMError("Install the SDK: pip install openai") from exc
        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_OPENAI_MODEL
        self.client = OpenAI()  # reads OPENAI_API_KEY

    def complete(self, system, messages, *, task="generic", max_tokens=1500) -> str:
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, *messages],
            )
        except Exception as exc:
            raise LLMError(f"OpenAI API error: {exc}") from exc
        return resp.choices[0].message.content or ""


# --------------------------------------------------------------------------- Mock
class MockLLM(BaseLLM):
    """
    Deterministic stand-in so the app, the tests and the demo work offline.
    It understands a handful of question patterns - enough to show the full pipeline.
    """

    name = "mock"

    def complete(self, system, messages, *, task="generic", max_tokens=1500) -> str:
        last = messages[-1]["content"] if messages else ""
        if task == "intent":
            return json.dumps({"intent": self._intent(last)})
        if task == "sql":
            return json.dumps(self._sql(last))
        if task == "explain":
            return json.dumps({
                "summary": "Here are the results of your query. (Offline demo mode: set ANTHROPIC_API_KEY "
                           "or OPENAI_API_KEY to get real written insights.)",
                "follow_ups": ["Show this as a chart", "Break this down by region", "Compare with the previous year"],
            })
        if task == "agent":
            return json.dumps(self._agent_step(messages))
        return "{}"

    # -- helpers
    @staticmethod
    def _question(text: str) -> str:
        m = re.search(r"Question:\s*(.*)", text, re.S)
        return (m.group(1) if m else text).strip().lower()

    def _intent(self, text: str) -> str:
        q = self._question(text)
        if re.search(r"\bwhy\b|\bcause\b|\breason\b|what explains", q):
            return "analysis"
        if re.search(r"what (tables|data|columns)|which tables|available data|schema", q):
            return "schema_info"
        if re.search(r"^(hi|hello|hey|thanks|thank you)\b|joke|weather", q):
            return "off_topic"
        if re.search(r"^(and|now|only|also|same)\b|\bthat\b", q):
            return "follow_up"
        return "data_query"

    def _sql(self, text: str) -> dict:
        q = self._question(text)
        year = re.search(r"\b(2024|2025)\b", q)
        date_filter = f" AND strftime('%Y', o.order_date) = '{year.group(1)}'" if year else ""
        top = re.search(r"top (\d+)", q)
        n = int(top.group(1)) if top else 5
        if re.search(r"how many departments|number of departments", q):
            return {"sql": "SELECT COUNT(*) AS department_count FROM departments",
                    "explanation": "Counts the rows in the departments table.", "assumptions": []}

        if "salary" in q and "department" in q:
            return {
                "sql": ("SELECT d.name AS department, ROUND(AVG(e.salary), 2) AS avg_salary FROM employees e "
                        "JOIN departments d ON d.id = e.department_id GROUP BY d.name "
                        f"ORDER BY avg_salary DESC LIMIT {n}"),
                "explanation": "Averages salary per department and keeps the highest.", "assumptions": []}
        if re.search(r"how many employees|headcount|employees (per|by) department", q):
            return {
                "sql": ("SELECT d.name AS department, COUNT(*) AS employee_count FROM employees e "
                        "JOIN departments d ON d.id = e.department_id GROUP BY d.name ORDER BY employee_count DESC"),
                "explanation": "Counts employees in each department.", "assumptions": []}
        if re.search(r"(sales|revenue).*month|monthly (sales|revenue)", q):
            return {
                "sql": ("SELECT strftime('%Y-%m', o.order_date) AS month, ROUND(SUM(o.total_amount), 2) AS total_sales "
                        f"FROM orders o WHERE o.status = 'completed'{date_filter} GROUP BY month ORDER BY month"),
                "explanation": "Sums completed order amounts for each month.",
                "assumptions": ["Sales counts completed orders only"]}
        if re.search(r"(sales|revenue).*categor", q):
            return {
                "sql": ("SELECT p.category, ROUND(SUM(o.total_amount), 2) AS total_sales FROM orders o "
                        f"JOIN products p ON p.id = o.product_id WHERE o.status = 'completed'{date_filter} "
                        "GROUP BY p.category ORDER BY total_sales DESC"),
                "explanation": "Sums completed sales per product category.", "assumptions": []}
        if re.search(r"(sales|revenue).*region", q):
            return {
                "sql": ("SELECT c.region, ROUND(SUM(o.total_amount), 2) AS total_sales FROM orders o "
                        f"JOIN customers c ON c.id = o.customer_id WHERE o.status = 'completed'{date_filter} "
                        "GROUP BY c.region ORDER BY total_sales DESC"),
                "explanation": "Sums completed sales per customer region.", "assumptions": []}
        if re.search(r"(top|best).*products?", q):
            return {
                "sql": ("SELECT p.name AS product, ROUND(SUM(o.total_amount), 2) AS total_sales FROM orders o "
                        f"JOIN products p ON p.id = o.product_id WHERE o.status = 'completed'{date_filter} "
                        f"GROUP BY p.name ORDER BY total_sales DESC LIMIT {n}"),
                "explanation": "Ranks products by completed sales.", "assumptions": []}
        return {"sql": None, "assumptions": [],
                "explanation": "The offline demo model only knows a few question patterns "
                               "(salary by department, headcount, sales by month/category/region, top products)."}

    def _agent_step(self, messages: list[dict[str, str]]) -> dict:
        observations = sum(1 for m in messages if m["role"] == "user" and m["content"].startswith("Observation"))
        script = [
            ("First, measure sales by month around the change.",
             "SELECT strftime('%Y-%m', order_date) AS month, ROUND(SUM(total_amount), 2) AS total_sales "
             "FROM orders WHERE status = 'completed' AND order_date >= '2025-01-01' AND order_date < '2025-05-01' "
             "GROUP BY month ORDER BY month"),
            ("Now see which product category changed most between February and March 2025.",
             "SELECT p.category, strftime('%Y-%m', o.order_date) AS month, ROUND(SUM(o.total_amount), 2) AS total_sales "
             "FROM orders o JOIN products p ON p.id = o.product_id WHERE o.status = 'completed' "
             "AND o.order_date >= '2025-02-01' AND o.order_date < '2025-04-01' GROUP BY p.category, month "
             "ORDER BY p.category, month"),
            ("Drill into Electronics by customer region.",
             "SELECT c.region, strftime('%Y-%m', o.order_date) AS month, ROUND(SUM(o.total_amount), 2) AS total_sales "
             "FROM orders o JOIN products p ON p.id = o.product_id JOIN customers c ON c.id = o.customer_id "
             "WHERE o.status = 'completed' AND p.category = 'Electronics' "
             "AND o.order_date >= '2025-02-01' AND o.order_date < '2025-04-01' "
             "GROUP BY c.region, month ORDER BY c.region, month"),
        ]
        if observations < len(script):
            thought, sql = script[observations]
            return {"thought": thought, "action": "run_sql", "sql": sql}
        return {"thought": "I have enough evidence.", "action": "final_answer",
                "answer": "Offline demo mode: the three queries above show the monthly trend, the category "
                          "breakdown and the regional drill-down. Set ANTHROPIC_API_KEY or OPENAI_API_KEY for a "
                          "written root-cause summary."}


# --------------------------------------------------------------------------- factory + JSON helpers
@lru_cache
def get_llm() -> BaseLLM:
    provider = (os.getenv("LLM_PROVIDER") or "").lower()
    if not provider:
        if os.getenv("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif os.getenv("OPENAI_API_KEY"):
            provider = "openai"
        else:
            provider = "mock"
    if provider == "anthropic":
        return AnthropicLLM()
    if provider == "openai":
        return OpenAILLM()
    if provider == "mock":
        return MockLLM()
    raise LLMError(f"Unknown LLM_PROVIDER '{provider}' (use anthropic, openai or mock)")


def extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output, tolerating ```json fences and stray prose."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("Model did not return JSON")
        try:
            value = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"Model returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMError("Model JSON was not an object")
    return value


def complete_json(
    llm: BaseLLM,
    system: str,
    messages: list[dict[str, str]],
    *,
    task: str,
    max_tokens: int = 1500,
    retries: int = 1,
) -> dict[str, Any]:
    """Call the LLM and return parsed JSON. Retries once with a corrective nudge."""
    msgs = list(messages)
    last_error: Exception | None = None
    for _ in range(retries + 1):
        raw = llm.complete(system, msgs, task=task, max_tokens=max_tokens)
        try:
            return extract_json(raw)
        except LLMError as exc:
            last_error = exc
            msgs = [*messages, {"role": "assistant", "content": raw},
                    {"role": "user", "content": "Your reply was not valid JSON. Reply again with ONLY the JSON object."}]
    raise LLMError(str(last_error))