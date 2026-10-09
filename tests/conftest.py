"""Shared fixtures: a throwaway database so tests never touch database/app.db."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from database.init_db import build_database  # noqa: E402


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "test.db"
    build_database(path, force=True)
    return str(path)


@pytest.fixture
def settings(db_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", db_path)
    from backend.config import get_settings
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()
