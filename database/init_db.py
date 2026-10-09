"""
PHASE 1 - Create the SQLite database and fill it with realistic sample data.

Usage:
    python database/init_db.py            # create database/app.db (skips if it exists)
    python database/init_db.py --force    # delete and rebuild

The data is deterministic (fixed random seed) and has a story baked in:
Electronics sales in the West region collapsed in March 2025 (a "stock-out"),
so the question "Why did sales decrease in March?" has a real answer for the
analysis agent to discover.
"""
from __future__ import annotations

import argparse
import os
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "database" / "app.db"

SCHEMA = """
CREATE TABLE departments (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE,
    location  TEXT NOT NULL
);

CREATE TABLE employees (
    id             INTEGER PRIMARY KEY,
    first_name     TEXT NOT NULL,
    last_name      TEXT NOT NULL,
    email          TEXT NOT NULL UNIQUE,
    department_id  INTEGER NOT NULL REFERENCES departments(id),
    job_title      TEXT NOT NULL,
    salary         REAL NOT NULL,
    hire_date      TEXT NOT NULL,          -- YYYY-MM-DD
    manager_id     INTEGER REFERENCES employees(id)
);

CREATE TABLE customers (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    region       TEXT NOT NULL,            -- North / South / East / West
    segment      TEXT NOT NULL,            -- Consumer / SMB / Enterprise
    signup_date  TEXT NOT NULL             -- YYYY-MM-DD
);

CREATE TABLE products (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,             -- Electronics / Furniture / Office Supplies / Software
    unit_price  REAL NOT NULL
);

CREATE TABLE orders (
    id            INTEGER PRIMARY KEY,
    customer_id   INTEGER NOT NULL REFERENCES customers(id),
    product_id    INTEGER NOT NULL REFERENCES products(id),
    order_date    TEXT NOT NULL,           -- YYYY-MM-DD
    quantity      INTEGER NOT NULL,
    total_amount  REAL NOT NULL,           -- quantity * unit_price after discount
    status        TEXT NOT NULL            -- completed / returned / cancelled
);

CREATE INDEX idx_employees_department ON employees(department_id);
CREATE INDEX idx_orders_date          ON orders(order_date);
CREATE INDEX idx_orders_customer      ON orders(customer_id);
CREATE INDEX idx_orders_product       ON orders(product_id);
"""

DEPARTMENTS = [
    # name, location, headcount, base salary
    ("Engineering", "Bengaluru", 14, 105_000),
    ("Data Science", "Bengaluru", 10, 112_000),
    ("Product", "Hyderabad", 8, 108_000),
    ("Sales", "Mumbai", 12, 78_000),
    ("Marketing", "Mumbai", 8, 72_000),
    ("Finance", "Pune", 7, 85_000),
    ("HR", "Pune", 5, 66_000),
    ("Customer Support", "Chennai", 10, 52_000),
]

TITLES = {
    "Engineering": ["Software Engineer", "Senior Software Engineer", "Staff Engineer", "Engineering Manager"],
    "Data Science": ["Data Analyst", "Data Scientist", "Senior Data Scientist", "ML Engineer"],
    "Product": ["Associate PM", "Product Manager", "Senior Product Manager", "Group PM"],
    "Sales": ["Sales Associate", "Account Executive", "Senior Account Executive", "Sales Manager"],
    "Marketing": ["Marketing Associate", "Content Strategist", "Growth Marketer", "Marketing Manager"],
    "Finance": ["Financial Analyst", "Accountant", "Senior Analyst", "Finance Manager"],
    "HR": ["HR Associate", "Recruiter", "HR Business Partner", "HR Manager"],
    "Customer Support": ["Support Agent", "Support Specialist", "Senior Specialist", "Support Lead"],
}
LEVEL_MULTIPLIER = [0.80, 1.00, 1.25, 1.55]

FIRST_NAMES = ["Aarav", "Vivaan", "Aditya", "Ishaan", "Rohan", "Kabir", "Arjun", "Karan", "Neha", "Priya",
               "Ananya", "Diya", "Kavya", "Meera", "Sneha", "Pooja", "Riya", "Isha", "Tanvi", "Nisha",
               "Rahul", "Amit", "Sanjay", "Vikram", "Manish", "Deepak", "Suresh", "Anil", "Lakshmi", "Divya"]
LAST_NAMES = ["Sharma", "Verma", "Gupta", "Iyer", "Nair", "Reddy", "Patel", "Mehta", "Singh", "Kumar",
              "Das", "Joshi", "Rao", "Menon", "Pillai", "Bose", "Chopra", "Kapoor", "Shetty", "Hegde"]

PRODUCTS = [
    # name, category, unit price
    ("Laptop Pro 14", "Electronics", 1450.0),
    ("Noise-Cancelling Headphones", "Electronics", 320.0),
    ("4K Monitor 27in", "Electronics", 480.0),
    ("Wireless Keyboard", "Electronics", 95.0),
    ("Ergonomic Chair", "Furniture", 410.0),
    ("Standing Desk", "Furniture", 640.0),
    ("Bookshelf", "Furniture", 180.0),
    ("Filing Cabinet", "Furniture", 150.0),
    ("Printer Paper (Case)", "Office Supplies", 42.0),
    ("Gel Pens (Box)", "Office Supplies", 18.0),
    ("Whiteboard Markers", "Office Supplies", 12.0),
    ("Sticky Notes (Pack)", "Office Supplies", 9.0),
    ("Analytics Suite (Annual)", "Software", 900.0),
    ("Design Toolkit (Annual)", "Software", 540.0),
    ("Antivirus (Annual)", "Software", 60.0),
    ("Project Tracker (Annual)", "Software", 240.0),
]
PRODUCT_WEIGHTS = [6, 8, 6, 12, 5, 4, 6, 4, 14, 14, 12, 12, 2, 3, 6, 5]

REGIONS = ["North", "South", "East", "West"]
REGION_WEIGHTS = [25, 25, 20, 30]
SEGMENTS = ["Consumer", "SMB", "Enterprise"]
SEGMENT_WEIGHTS = [50, 35, 15]
COMPANY_WORDS = ["Apex", "Nimbus", "Orbit", "Lotus", "Zenith", "Vertex", "Pioneer", "Summit", "Harbor",
                 "Crescent", "Falcon", "Maple", "Indigo", "Quantum", "Saffron", "Everest"]
COMPANY_SUFFIX = ["Traders", "Labs", "Solutions", "Industries", "Retail", "Systems", "Co", "Group"]

DATA_START = date(2024, 1, 1)
DATA_END = date(2025, 12, 31)


def _month_starts():
    d = DATA_START
    while d <= DATA_END:
        yield d
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def _days_in_month(d: date) -> int:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return (nxt - d).days


def build_database(db_path: str | os.PathLike = DEFAULT_DB_PATH, force: bool = False) -> Path:
    """Create the database file (and return its path)."""
    db_path = Path(db_path)
    if db_path.exists():
        if not force:
            return db_path
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    rng = random.Random(42)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    # --- departments + employees -------------------------------------------------
    emp_id = 0
    used_emails: set[str] = set()
    for dept_id, (name, location, headcount, base) in enumerate(DEPARTMENTS, start=1):
        conn.execute("INSERT INTO departments VALUES (?,?,?)", (dept_id, name, location))
        manager_of_dept = None
        for i in range(headcount):
            emp_id += 1
            level = 3 if i == 0 else rng.choices([0, 1, 2], weights=[40, 40, 20])[0]
            first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
            email = f"{first}.{last}@example.com".lower()
            while email in used_emails:
                email = f"{first}.{last}{rng.randint(2, 99)}@example.com".lower()
            used_emails.add(email)
            salary = round(base * LEVEL_MULTIPLIER[level] * rng.uniform(0.93, 1.07), -2)
            hired = date(2016, 1, 1) + timedelta(days=rng.randint(0, 365 * 9))
            if i == 0:
                manager_of_dept = emp_id
            conn.execute(
                "INSERT INTO employees VALUES (?,?,?,?,?,?,?,?,?)",
                (emp_id, first, last, email, dept_id, TITLES[name][level], salary,
                 hired.isoformat(), None if i == 0 else manager_of_dept),
            )

    # --- customers -----------------------------------------------------------------
    n_customers = 180
    customer_region: dict[int, str] = {}
    for cid in range(1, n_customers + 1):
        region = rng.choices(REGIONS, weights=REGION_WEIGHTS)[0]
        customer_region[cid] = region
        name = f"{rng.choice(COMPANY_WORDS)} {rng.choice(COMPANY_SUFFIX)} {cid}"
        signup = date(2022, 1, 1) + timedelta(days=rng.randint(0, 800))
        conn.execute("INSERT INTO customers VALUES (?,?,?,?,?)",
                     (cid, name, region, rng.choices(SEGMENTS, weights=SEGMENT_WEIGHTS)[0], signup.isoformat()))

    # --- products ------------------------------------------------------------------
    for pid, (name, category, price) in enumerate(PRODUCTS, start=1):
        conn.execute("INSERT INTO products VALUES (?,?,?,?)", (pid, name, category, price))

    # --- orders ----------------------------------------------------------------------
    order_id = 0
    for idx, month in enumerate(_month_starts()):
        seasonal = {11: 1.25, 12: 1.35, 1: 0.9, 2: 0.95}.get(month.month, 1.0)
        n_orders = int((95 + idx * 2.5) * seasonal)
        for _ in range(n_orders):
            cid = rng.randint(1, n_customers)
            pid = rng.choices(range(1, len(PRODUCTS) + 1), weights=PRODUCT_WEIGHTS)[0]
            name, category, price = PRODUCTS[pid - 1]
            region = customer_region[cid]

            # The planted story: Electronics stock-out, worst in the West, March 2025.
            if month == date(2025, 3, 1) and category == "Electronics":
                if rng.random() < (0.90 if region == "West" else 0.55):
                    continue

            order_id += 1
            qty = rng.choices([1, 2, 3, 4, 5], weights=[50, 25, 12, 8, 5])[0]
            discount = rng.choice([1.0, 1.0, 1.0, 0.95, 0.9])
            order_day = month + timedelta(days=rng.randint(0, _days_in_month(month) - 1))
            status = rng.choices(["completed", "returned", "cancelled"], weights=[90, 6, 4])[0]
            conn.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?)",
                         (order_id, cid, pid, order_day.isoformat(), qty,
                          round(qty * price * discount, 2), status))

    conn.commit()
    conn.execute("ANALYZE")
    conn.close()
    return db_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the sample SQLite database.")
    parser.add_argument("--force", action="store_true", help="Rebuild even if the file exists")
    parser.add_argument("--path", default=os.getenv("DB_PATH", str(DEFAULT_DB_PATH)))
    args = parser.parse_args()

    existed = Path(args.path).exists()
    path = build_database(args.path, force=args.force)
    if existed and not args.force:
        print(f"Database already exists at {path} (use --force to rebuild).")
        return
    conn = sqlite3.connect(path)
    print(f"Created {path}")
    for table in ("departments", "employees", "customers", "products", "orders"):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<12} {count:>6} rows")
    conn.close()


if __name__ == "__main__":
    main()
