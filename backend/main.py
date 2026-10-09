"""
PHASE 5 - FastAPI backend.

Run:   uvicorn backend.main:app --reload        (from the project root)
Docs:  http://localhost:8000/docs
"""
from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .assistant import Assistant
from .charts import suggest_chart
from .config import get_settings
from .query_executor import QueryExecutionError, execute_query
from .schemas import (ChatRequest, ChatResponse, HealthResponse, QueryRequest, QueryResponse, SchemaResponse)

settings = get_settings()

app = FastAPI(
    title="AI SQL Assistant",
    version="1.0.0",
    description="Ask questions in plain English; get validated, read-only SQL, results, charts and explanations.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


@lru_cache
def get_assistant() -> Assistant:
    return Assistant()


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Optional shared-secret auth: enabled only when API_KEY is set in the environment."""
    expected = get_settings().api_key
    if expected and not secrets.compare_digest(x_api_key or "", expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    assistant = get_assistant()
    return HealthResponse(status="ok", llm_provider=assistant.llm.name,
                          database=Path(settings.db_path).exists())


@app.get("/api/schema", response_model=SchemaResponse, dependencies=[Depends(require_api_key)], tags=["data"])
def schema() -> dict:
    return get_assistant().schema


@app.post("/api/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)], tags=["assistant"])
def chat(req: ChatRequest) -> dict:
    """Natural-language question -> SQL -> result -> explanation."""
    return get_assistant().chat(req.question, req.session_id)


@app.delete("/api/sessions/{session_id}", dependencies=[Depends(require_api_key)], tags=["assistant"])
def reset_session(session_id: str) -> dict:
    return {"cleared": get_assistant().sessions.clear(session_id)}


@app.post("/api/query", response_model=QueryResponse, dependencies=[Depends(require_api_key)], tags=["data"])
def run_sql(req: QueryRequest) -> QueryResponse:
    """Run hand-written SQL through the same validator + read-only executor (no LLM involved)."""
    assistant = get_assistant()
    check = assistant.validator.validate(req.sql)
    if not check.ok:
        return QueryResponse(ok=False, errors=check.errors, warnings=check.warnings)
    try:
        result = execute_query(check.sql, settings.db_path, settings.max_rows, settings.query_timeout_s)
    except QueryExecutionError as exc:
        return QueryResponse(ok=False, sql=check.sql, errors=[str(exc)])
    return QueryResponse(
        ok=True, sql=check.sql, warnings=check.warnings, optimizations=check.hints,
        columns=result.columns, rows=result.rows, row_count=result.row_count, truncated=result.truncated,
        chart=suggest_chart(result.columns, result.rows),
    )
