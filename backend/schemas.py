"""Pydantic models = the API contract (request validation + response documentation)."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000, examples=["Show sales by month"])
    session_id: Optional[str] = Field(None, max_length=64, description="Send the id from the previous reply to keep context")


class QueryRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=4000)


class ChartSpec(BaseModel):
    type: str                     # bar | line | pie
    x: str
    y: list[str]
    series: Optional[str] = None
    title: Optional[str] = None


class AgentStepModel(BaseModel):
    thought: str
    sql: Optional[str] = None
    columns: list[str] = []
    rows: list[list[Any]] = []
    error: Optional[str] = None


class ChatResponse(BaseModel):
    session_id: str
    question: str
    intent: Optional[str] = None
    answer: str = ""
    sql: Optional[str] = None
    query_explanation: Optional[str] = None
    assumptions: list[str] = []
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    truncated: bool = False
    chart: Optional[ChartSpec] = None
    follow_ups: list[str] = []
    warnings: list[str] = []
    optimizations: list[str] = []
    steps: list[AgentStepModel] = []
    error: Optional[str] = None
    elapsed_ms: float = 0.0


class QueryResponse(BaseModel):
    ok: bool
    sql: str = ""
    errors: list[str] = []
    warnings: list[str] = []
    optimizations: list[str] = []
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    truncated: bool = False
    chart: Optional[ChartSpec] = None


class ColumnInfo(BaseModel):
    name: str
    type: str
    pk: bool = False
    references: Optional[str] = None
    values: Optional[list[str]] = None
    min: Optional[str] = None
    max: Optional[str] = None


class TableInfo(BaseModel):
    name: str
    row_count: int
    columns: list[ColumnInfo]


class SchemaResponse(BaseModel):
    tables: list[TableInfo]


class HealthResponse(BaseModel):
    status: str
    llm_provider: str
    database: bool
