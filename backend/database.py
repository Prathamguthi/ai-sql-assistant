"""
Database access helpers + schema understanding.

`get_schema_info()` introspects the live database so the LLM always sees the real
tables, columns, foreign keys, allowed values and date ranges - nothing is hard-coded.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .config import get_settings

LOW_CARDINALITY_LIMIT = 12
SKIP_VALUE_LISTING = {"email"}


def connect_readonly(db_path: str | os.PathLike | None = None) -> sqlite3.Connection:
    """Open the database in read-only mode (writes fail at the SQLite level)."""
    path = Path(db_path or get_settings().db_path).resolve()
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found at {path}. Run `python database/init_db.py` first."
        )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_table_names(db_path: str | None = None) -> list[str]:
    conn = connect_readonly(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]
    finally:
        conn.close()


def get_schema_info(db_path: str | None = None) -> dict:
    """
    Return a JSON-friendly description of the database:
    {tables: [{name, row_count, columns: [{name, type, pk, references, values, min, max}]}]}
    """
    conn = connect_readonly(db_path)
    try:
        tables = []
        for name in get_table_names(db_path):
            fks = {
                r["from"]: f'{r["table"]}.{r["to"]}'
                for r in conn.execute(f'PRAGMA foreign_key_list("{name}")').fetchall()
            }
            row_count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            columns = []
            for col in conn.execute(f'PRAGMA table_info("{name}")').fetchall():
                info = {
                    "name": col["name"],
                    "type": col["type"] or "TEXT",
                    "pk": bool(col["pk"]),
                    "references": fks.get(col["name"]),
                }
                cname = col["name"]
                ctype = (col["type"] or "").upper()
                if "date" in cname.lower():
                    lo, hi = conn.execute(f'SELECT MIN("{cname}"), MAX("{cname}") FROM "{name}"').fetchone()
                    info["min"], info["max"] = lo, hi
                elif ctype == "TEXT" and cname.lower() not in SKIP_VALUE_LISTING:
                    vals = conn.execute(
                        f'SELECT DISTINCT "{cname}" FROM "{name}" LIMIT {LOW_CARDINALITY_LIMIT + 1}'
                    ).fetchall()
                    if 0 < len(vals) <= LOW_CARDINALITY_LIMIT:
                        info["values"] = sorted(str(v[0]) for v in vals if v[0] is not None)
                columns.append(info)
            tables.append({"name": name, "row_count": row_count, "columns": columns})
        return {"tables": tables}
    finally:
        conn.close()


def schema_to_prompt(schema: dict) -> str:
    """Render the schema as compact text for the LLM prompt."""
    lines: list[str] = []
    for t in schema["tables"]:
        lines.append(f'TABLE {t["name"]}  ({t["row_count"]} rows)')
        for c in t["columns"]:
            parts = [f'  - {c["name"]} {c["type"]}']
            if c["pk"]:
                parts.append("PRIMARY KEY")
            if c.get("references"):
                parts.append(f'-> {c["references"]}')
            if "min" in c:
                parts.append(f'(range {c["min"]} .. {c["max"]})')
            if c.get("values"):
                parts.append("(values: " + ", ".join(c["values"]) + ")")
            lines.append(" ".join(parts))
        lines.append("")
    return "\n".join(lines).strip()


def get_schema_prompt(db_path: str | None = None) -> str:
    return schema_to_prompt(get_schema_info(db_path))
