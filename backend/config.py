"""Central configuration. Everything is read from environment variables (or .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    db_path: str
    max_rows: int               # hard cap on rows returned to the client
    query_timeout_s: float      # wall-clock limit for one query
    max_sql_retries: int        # LLM self-repair attempts after a failed query
    history_turns: int          # past turns given to the LLM for follow-up questions
    agent_max_steps: int        # max queries the analysis agent may run
    max_question_chars: int
    api_key: str | None         # if set, clients must send X-API-Key
    cors_origins: list[str]


@lru_cache
def get_settings() -> Settings:
    origins = os.getenv("CORS_ORIGINS", "http://localhost:8501")
    return Settings(
        db_path=os.getenv("DB_PATH", str(ROOT / "database" / "app.db")),
        max_rows=_int("MAX_ROWS", 500),
        query_timeout_s=_float("QUERY_TIMEOUT_S", 5.0),
        max_sql_retries=_int("MAX_SQL_RETRIES", 2),
        history_turns=_int("HISTORY_TURNS", 3),
        agent_max_steps=_int("AGENT_MAX_STEPS", 6),
        max_question_chars=_int("MAX_QUESTION_CHARS", 1000),
        api_key=os.getenv("API_KEY") or None,
        cors_origins=[o.strip() for o in origins.split(",") if o.strip()],
    )
