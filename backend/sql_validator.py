"""
PHASE 4 - SQL validation + security.

LLM output is untrusted input. Before anything touches the database we check that it is
exactly one read-only SELECT over known tables, strip comments, enforce a LIMIT, and dry-run
it with EXPLAIN QUERY PLAN (which also yields optimization hints).

How "SQL injection protection" works in this app:
  * user text is never concatenated into SQL by our code - only the LLM writes SQL
  * the LLM's SQL is validated here (allow-list: SELECT/WITH only, single statement)
  * the executor is read-only with an authorizer (see query_executor.py) as a second wall

The scanner below is a small hand-written tokenizer, so keywords inside string literals
('please DROP by') are not false positives and comments cannot hide anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

MAX_SQL_LENGTH = 4000

FORBIDDEN_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "create", "truncate", "attach", "detach",
    "pragma", "vacuum", "reindex", "analyze", "begin", "commit", "rollback", "savepoint",
    "release", "grant", "revoke", "upsert",
}
FORBIDDEN_FUNCTIONS = {
    "load_extension", "readfile", "writefile", "edit", "fts3_tokenizer", "zeroblob", "randomblob",
}


@dataclass
class ValidationResult:
    ok: bool
    sql: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)       # optimization suggestions
    tables: list[str] = field(default_factory=list)
    blocked: bool = False   # True = security rule violated (do not retry with the LLM)


def _scan(sql: str) -> tuple[str, str]:
    """
    Return (clean_sql, code_only).
      clean_sql  - original SQL with comments removed
      code_only  - lower-friendly version for scanning: comments removed, single-quoted string
                   contents blanked, quoted identifiers unquoted, whitespace collapsed
    Raises ValueError on unterminated strings/comments.
    """
    clean: list[str] = []
    code: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if c == "-" and nxt == "-":
            j = sql.find("\n", i)
            i = n if j == -1 else j
            clean.append(" "); code.append(" ")
            continue
        if c == "/" and nxt == "*":
            j = sql.find("*/", i + 2)
            if j == -1:
                raise ValueError("Unterminated comment")
            i = j + 2
            clean.append(" "); code.append(" ")
            continue
        if c in ("'", '"', "`", "["):
            close = "]" if c == "[" else c
            j = i + 1
            while True:
                if j >= n:
                    raise ValueError("Unterminated string or quoted identifier")
                if sql[j] == close:
                    if close != "]" and j + 1 < n and sql[j + 1] == close:   # doubled quote escape
                        j += 2
                        continue
                    break
                j += 1
            literal = sql[i:j + 1]
            clean.append(literal)
            if c == "'":
                code.append("''")                      # blank string contents
            else:
                code.append(" " + literal[1:-1].replace(close * 2, close) + " ")   # keep identifier name
            i = j + 1
            continue
        clean.append(c); code.append(c)
        i += 1
    return "".join(clean), re.sub(r"\s+", " ", "".join(code)).strip()


def _top_level(code: str) -> str:
    """Blank everything inside parentheses so we can look at the outermost query only."""
    depth, out = 0, []
    for ch in code:
        if ch == "(":
            depth += 1
            out.append(" ")
        elif ch == ")":
            depth = max(0, depth - 1)
            out.append(" ")
        else:
            out.append(ch if depth == 0 else " ")
    return "".join(out)


class SQLValidator:
    def __init__(
        self,
        known_tables: Iterable[str] | None = None,
        max_rows: int = 500,
        dry_run: Callable[[str], list[str]] | None = None,
    ):
        self.known_tables = {t.lower() for t in known_tables} if known_tables is not None else None
        self.max_rows = max_rows
        self.dry_run = dry_run

    def validate(self, sql: str) -> ValidationResult:
        res = ValidationResult(ok=False)

        def block(msg: str) -> ValidationResult:
            res.errors.append(msg)
            res.blocked = True
            return res

        if not sql or not sql.strip():
            res.errors.append("Empty SQL.")
            return res
        if len(sql) > MAX_SQL_LENGTH:
            return block(f"SQL too long (max {MAX_SQL_LENGTH} characters).")

        try:
            clean, code = _scan(sql)
        except ValueError as exc:
            return block(str(exc))

        clean = clean.strip().rstrip(";").strip()
        code = code.rstrip(";").strip()
        if not code:
            res.errors.append("Empty SQL after removing comments.")
            return res

        # 1) exactly one statement
        if ";" in code:
            return block("Multiple statements are not allowed.")

        # 2) must start with SELECT / WITH
        if not re.match(r"(select|with)\b", code, re.I):
            return block("Only SELECT queries are allowed.")

        lowered = code.lower()
        words = set(re.findall(r"[a-z_][a-z0-9_]*", lowered))

        # 3) forbidden keywords / functions / system tables
        bad_kw = sorted(words & FORBIDDEN_KEYWORDS)
        if bad_kw:
            return block(f"Forbidden keyword(s): {', '.join(k.upper() for k in bad_kw)}.")
        called = set(re.findall(r"\b([a-z_][a-z0-9_]*)\s*\(", lowered))
        bad_fn = sorted(called & FORBIDDEN_FUNCTIONS)
        if bad_fn:
            return block(f"Forbidden function(s): {', '.join(bad_fn)}.")
        if re.search(r"\bsqlite_[a-z0-9_]*", lowered):
            return block("Access to SQLite system tables is not allowed.")

        # 4) every referenced table must exist (CTE names excluded)
        ctes = set(re.findall(
            r"(?:\bwith\s+(?:recursive\s+)?|,\s*)([a-z_][a-z0-9_]*)\s*(?:\([^)]*\)\s*)?as\s*"
            r"(?:not\s+materialized\s*|materialized\s*)?\(", lowered))
        referenced = []
        for m in re.finditer(r"(?<!distinct )\b(?:from|join)\s+([a-z_][a-z0-9_]*)(?:\.([a-z_][a-z0-9_]*))?", lowered):
            referenced.append(m.group(2) or m.group(1))
        res.tables = sorted(set(referenced) - ctes)
        if self.known_tables is not None:
            unknown = [t for t in res.tables if t not in self.known_tables]
            if unknown:
                res.errors.append(
                    f"Unknown table(s): {', '.join(unknown)}. Available: {', '.join(sorted(self.known_tables))}."
                )
                return res

        # 5) enforce LIMIT
        top = _top_level(lowered)
        limit_match = re.search(r"\blimit\s+(\d+)", top)
        final_sql = clean
        if not limit_match:
            final_sql = f"{clean}\nLIMIT {self.max_rows}"
            res.warnings.append(f"No LIMIT found - added LIMIT {self.max_rows}.")
        elif int(limit_match.group(1)) > self.max_rows:
            res.warnings.append(f"LIMIT {limit_match.group(1)} exceeds {self.max_rows}; results will be truncated.")

        # 6) static optimization hints
        if re.search(r"select\s+(?:distinct\s+)?(?:\w+\.)?\*", lowered):
            res.hints.append("SELECT * returns every column - select only the columns you need.")
        if re.search(r"like\s+'%", clean, re.I):
            res.hints.append("A LIKE pattern starting with % cannot use an index.")

        # 7) dry run: catches syntax errors / unknown columns, and gives the query plan
        if self.dry_run is not None:
            try:
                plan = self.dry_run(final_sql)
            except Exception as exc:       # QueryExecutionError / sqlite3.Error
                res.errors.append(str(exc))
                return res
            has_where = " where " in f" {lowered} "
            for line in plan:
                m = re.match(r"SCAN (?:TABLE )?(\w+)(.*)", line)
                if m and has_where and "INDEX" not in m.group(2).upper():
                    res.hints.append(
                        f"Full table scan on '{m.group(1)}' despite a WHERE filter - "
                        f"consider an index on the filtered column."
                    )

        res.sql = final_sql
        res.ok = True
        return res
