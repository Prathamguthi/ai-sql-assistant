"""PHASE 9 - the validator is the security core, so it gets the most tests."""
import pytest

from backend.query_executor import make_dry_run
from backend.sql_validator import SQLValidator

TABLES = ["employees", "departments", "customers", "products", "orders"]


@pytest.fixture
def validator():
    return SQLValidator(TABLES, max_rows=500)


# ---------------------------------------------------------------- things that must be allowed
@pytest.mark.parametrize("sql", [
    "SELECT * FROM employees",
    "select department_id, avg(salary) from employees group by department_id",
    "SELECT d.name, AVG(e.salary) FROM employees e JOIN departments d ON d.id = e.department_id GROUP BY d.name",
    "WITH t AS (SELECT * FROM orders) SELECT COUNT(*) FROM t",
    "SELECT 'DROP TABLE users' AS harmless FROM departments",           # keyword inside a string literal
    "SELECT name FROM departments WHERE name = 'it''s; fine'",         # escaped quote + semicolon in string
    "SELECT * FROM employees;",                                          # trailing semicolon is fine
    "SELECT replace(first_name, 'a', 'b') FROM employees",             # REPLACE() function is not REPLACE INTO
    "SELECT created_at_like FROM (SELECT 1 AS created_at_like) ",       # keyword as part of a longer word
])
def test_allows_safe_queries(validator, sql):
    result = validator.validate(sql)
    assert result.ok, result.errors


# ---------------------------------------------------------------- things that must be blocked
@pytest.mark.parametrize("sql", [
    "DROP TABLE employees",
    "DELETE FROM employees WHERE 1=1",
    "UPDATE employees SET salary = 0",
    "INSERT INTO departments VALUES (99, 'x', 'y')",
    "SELECT 1; DROP TABLE employees",                                    # stacked statements
    "SELECT 1;; SELECT 2",
    "PRAGMA table_info(employees)",
    "ATTACH DATABASE 'x.db' AS x",
    "CREATE TABLE evil (a int)",
    "ALTER TABLE employees ADD COLUMN x int",
    "SELECT * FROM sqlite_master",
    "SELECT * FROM sqlite_schema",
    "SELECT load_extension('evil.so')",
    "SELECT readfile('/etc/passwd')",
    "SELECT /* DROP */ 1; /* x */ DROP TABLE orders",                  # comment tricks
    "-- harmless\nDROP TABLE employees",
    "/* x */ DELETE FROM orders",
    "SELECT * FROM employees WHERE name = 'unterminated",
    "EXPLAIN SELECT 1",
    "VALUES (1)",
    "WITH x AS (SELECT 1) DELETE FROM employees",
])
def test_blocks_dangerous_queries(validator, sql):
    result = validator.validate(sql)
    assert not result.ok
    assert result.blocked


def test_rejects_empty(validator):
    assert not validator.validate("   ").ok
    assert not validator.validate("-- just a comment").ok


def test_rejects_oversized_sql(validator):
    assert not validator.validate("SELECT 1 FROM employees WHERE id IN (" + "1," * 3000 + "1)").ok


# ---------------------------------------------------------------- behaviour
def test_unknown_table_is_reported_but_not_blocked(validator):
    result = validator.validate("SELECT * FROM salaries")
    assert not result.ok and not result.blocked
    assert "salaries" in result.errors[0]


def test_cte_names_are_not_unknown_tables(validator):
    assert validator.validate("WITH recent AS (SELECT * FROM orders) SELECT * FROM recent").ok


def test_adds_limit_when_missing(validator):
    result = validator.validate("SELECT * FROM employees")
    assert result.sql.endswith("LIMIT 500")
    assert any("LIMIT" in w for w in result.warnings)


def test_keeps_existing_limit_case_insensitive(validator):
    result = validator.validate("SELECT * FROM employees LIMIT 5")
    assert result.sql.count("LIMIT") == 1 and not result.warnings


def test_subquery_limit_does_not_count_as_top_level(validator):
    result = validator.validate("SELECT * FROM (SELECT * FROM employees LIMIT 3)")
    assert result.sql.endswith("LIMIT 500")


def test_comments_are_stripped(validator):
    result = validator.validate("SELECT id -- the id\nFROM employees /* all */ LIMIT 1")
    assert "--" not in result.sql and "/*" not in result.sql


def test_hints_for_select_star(validator):
    assert any("SELECT *" in h for h in validator.validate("SELECT * FROM employees").hints)


# ---------------------------------------------------------------- with the real DB dry-run
def test_dry_run_catches_unknown_column(db_path):
    v = SQLValidator(TABLES, 500, make_dry_run(db_path))
    result = v.validate("SELECT no_such_column FROM employees")
    assert not result.ok and not result.blocked
    assert "no_such_column" in result.errors[0]


def test_dry_run_flags_unindexed_filter(db_path):
    v = SQLValidator(TABLES, 500, make_dry_run(db_path))
    result = v.validate("SELECT id FROM orders WHERE quantity = 3")
    assert result.ok
    assert any("Full table scan" in h for h in result.hints)


def test_indexed_filter_has_no_scan_hint(db_path):
    v = SQLValidator(TABLES, 500, make_dry_run(db_path))
    result = v.validate("SELECT id FROM orders WHERE order_date >= '2025-01-01'")
    assert result.ok
    assert not any("Full table scan" in h for h in result.hints)
