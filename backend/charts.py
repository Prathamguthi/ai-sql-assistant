"""
PHASE 7 - Chart generation.

Rather than letting the LLM invent chart code, we derive a small, safe chart *spec* from the
shape of the result. The frontend renders the spec. Users can request a type
("as a pie chart", "line chart"), otherwise we pick one:

    time-like column + numbers      -> line
    category column + numbers       -> bar
    two label columns + one number  -> multi-series line/bar (spec has "series")
"""
from __future__ import annotations

import re
from typing import Any

TIME_NAME = re.compile(r"(month|date|day|week|quarter|year|period)", re.I)
TIME_VALUE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$|^\d{4}-q[1-4]$", re.I)
MAX_CATEGORIES = 60


def requested_chart_type(question: str) -> str | None:
    q = question.lower()
    if re.search(r"\bpie\b|\bdonut\b", q):
        return "pie"
    if re.search(r"\bline (chart|graph)|\btrend\b|\bover time\b", q):
        return "line"
    if re.search(r"\bbar (chart|graph)|\bhistogram\b", q):
        return "bar"
    return None


def wants_chart(question: str) -> bool:
    return bool(re.search(r"\bchart\b|\bgraph\b|\bplot\b|\bvisuali[sz]e\b|\bpie\b", question.lower()))


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def suggest_chart(columns: list[str], rows: list[list[Any]], requested: str | None = None) -> dict | None:
    if len(rows) < 2 or len(columns) < 2 or len(rows) > MAX_CATEGORIES * 5:
        return None

    numeric, labels = [], []
    for i, name in enumerate(columns):
        values = [r[i] for r in rows if r[i] is not None]
        is_num = bool(values) and all(_is_number(v) for v in values)
        # a numeric "year"/"month" column is a label, not a measure
        if is_num and not TIME_NAME.search(name) or (is_num and name.lower().endswith(("_count", "_total"))):
            numeric.append(i)
        else:
            labels.append(i)
    if not numeric or not labels:
        return None

    def is_time(i: int) -> bool:
        if TIME_NAME.search(columns[i]):
            return True
        vals = [str(r[i]) for r in rows[:5] if r[i] is not None]
        return bool(vals) and all(TIME_VALUE.match(v) for v in vals)

    time_cols = [i for i in labels if is_time(i)]
    x = time_cols[0] if time_cols else labels[0]
    series = next((i for i in labels if i != x), None)

    if series is not None:
        if len(numeric) != 1:
            return None
        chart_type = requested if requested in ("bar", "line") else ("line" if time_cols else "bar")
        return {"type": chart_type, "x": columns[x], "y": [columns[numeric[0]]],
                "series": columns[series], "title": f"{columns[numeric[0]]} by {columns[x]} and {columns[series]}"}

    chart_type = requested or ("line" if time_cols else "bar")
    if chart_type == "pie" and (len(rows) > 12 or len(numeric) != 1):
        chart_type = "bar"
    y = [columns[i] for i in numeric][:3]
    return {"type": chart_type, "x": columns[x], "y": y, "series": None,
            "title": f'{", ".join(y)} by {columns[x]}'}
