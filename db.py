from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from flask import current_app, g


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0),
    transaction_type TEXT NOT NULL CHECK (transaction_type IN ('income', 'expense')),
    transaction_date TEXT NOT NULL,
    vendor TEXT NOT NULL DEFAULT '',
    payment_method TEXT NOT NULL DEFAULT 'cash' CHECK (payment_method IN ('cash', 'bank', 'card', 'digital_wallet')),
    notes TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    receipt_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id);
CREATE TABLE IF NOT EXISTS receipt_scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    extracted_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'review' CHECK (status IN ('review', 'saved', 'rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, file_hash)
);
CREATE INDEX IF NOT EXISTS idx_receipt_scans_user ON receipt_scans(user_id);
CREATE TABLE IF NOT EXISTS transaction_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    UNIQUE(user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_transaction_categories_user ON transaction_categories(user_id);
CREATE TABLE IF NOT EXISTS goods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_price REAL NOT NULL CHECK (unit_price >= 0),
    category TEXT NOT NULL DEFAULT 'General',
    purchase_date TEXT NOT NULL DEFAULT '1970-01-01',
    vendor TEXT NOT NULL DEFAULT '',
    warranty_expiry TEXT,
    receipt_path TEXT,
    photo_path TEXT,
    notes TEXT NOT NULL DEFAULT '',
    estimated_value REAL NOT NULL DEFAULT 0 CHECK (estimated_value >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_goods_user ON goods(user_id);
CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category TEXT,
    period TEXT NOT NULL DEFAULT 'monthly' CHECK (period IN ('weekly', 'monthly', 'yearly')),
    limit_amount REAL NOT NULL CHECK (limit_amount >= 0),
    rollover_enabled INTEGER NOT NULL DEFAULT 0 CHECK (rollover_enabled IN (0, 1)),
    rollover_amount REAL NOT NULL DEFAULT 0 CHECK (rollover_amount >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, category, period)
);
CREATE INDEX IF NOT EXISTS idx_budgets_user_period ON budgets(user_id, period);
CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    merchant TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0),
    receipt_date TEXT NOT NULL,
    file_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_receipts_user ON receipts(user_id);
CREATE TABLE IF NOT EXISTS recurring_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0),
    frequency TEXT NOT NULL,
    next_due_date TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    category TEXT NOT NULL DEFAULT 'Bills',
    payment_method TEXT NOT NULL DEFAULT 'bank',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'cancelled')),
    vendor TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    price_history TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_recurring_expenses_user ON recurring_expenses(user_id);
CREATE TABLE IF NOT EXISTS recurring_payment_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recurring_id INTEGER NOT NULL REFERENCES recurring_expenses(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    due_date TEXT NOT NULL,
    transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    reminder_sent INTEGER NOT NULL DEFAULT 0 CHECK (reminder_sent IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(recurring_id, due_date)
);
CREATE INDEX IF NOT EXISTS idx_recurring_runs_user ON recurring_payment_runs(user_id, due_date);
CREATE TABLE IF NOT EXISTS savings_goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    target_amount REAL NOT NULL CHECK (target_amount > 0),
    current_amount REAL NOT NULL DEFAULT 0 CHECK (current_amount >= 0),
    target_date TEXT,
    goal_type TEXT NOT NULL DEFAULT 'savings' CHECK (goal_type IN ('savings', 'emergency')),
    monthly_contribution REAL NOT NULL DEFAULT 0 CHECK (monthly_contribution >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_savings_goals_user ON savings_goals(user_id);
CREATE TABLE IF NOT EXISTS shared_wallets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS shared_wallet_members (
    wallet_id INTEGER NOT NULL REFERENCES shared_wallets(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'editor' CHECK (role IN ('owner', 'editor', 'viewer')),
    PRIMARY KEY (wallet_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_wallet_members_user ON shared_wallet_members(user_id);
CREATE TABLE IF NOT EXISTS wallet_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_id INTEGER NOT NULL REFERENCES shared_wallets(id) ON DELETE CASCADE,
    paid_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    expense_date TEXT NOT NULL,
    split_method TEXT NOT NULL CHECK (split_method IN ('equal', 'proportional')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wallet_expenses_wallet ON wallet_expenses(wallet_id);
CREATE TABLE IF NOT EXISTS wallet_expense_splits (
    expense_id INTEGER NOT NULL REFERENCES wallet_expenses(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    share_amount REAL NOT NULL CHECK (share_amount >= 0),
    proportion REAL,
    PRIMARY KEY (expense_id, user_id)
);
CREATE TABLE IF NOT EXISTS wallet_settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_id INTEGER NOT NULL REFERENCES shared_wallets(id) ON DELETE CASCADE,
    from_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    to_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    amount REAL NOT NULL CHECK (amount > 0),
    settled_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_wallet_settlements_wallet ON wallet_settlements(wallet_id);
"""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        database = Path(current_app.config["DATABASE"])
        database.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(database)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_: Any = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    migrate_legacy_tables(db)
    migrate_wallet_member_roles(db)
    db.executescript(SCHEMA)
    if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='budgets_legacy'").fetchone():
        db.execute(
            """
            INSERT INTO budgets (user_id, category, period, limit_amount)
            SELECT user_id, category, 'monthly', limit_amount
            FROM budgets_legacy
            WHERE NOT EXISTS (
                SELECT 1 FROM budgets b
                WHERE b.user_id = budgets_legacy.user_id
                  AND b.category = budgets_legacy.category
                  AND b.period = 'monthly'
            )
            """
        )
        db.execute("DROP TABLE budgets_legacy")
    db.commit()


def migrate_wallet_member_roles(db: sqlite3.Connection) -> None:
    """Upgrade the original owner/member wallet roles without losing members."""
    table = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='shared_wallet_members'"
    ).fetchone()
    if table and "role IN ('owner', 'member')" in (table["sql"] or ""):
        db.execute("ALTER TABLE shared_wallet_members RENAME TO shared_wallet_members_legacy")
        db.execute(
            """
            CREATE TABLE shared_wallet_members (
                wallet_id INTEGER NOT NULL REFERENCES shared_wallets(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL DEFAULT 'editor' CHECK (role IN ('owner', 'editor', 'viewer')),
                PRIMARY KEY (wallet_id, user_id)
            )
            """
        )
        db.execute(
            """
            INSERT INTO shared_wallet_members (wallet_id, user_id, role)
            SELECT wallet_id, user_id, CASE WHEN role='owner' THEN 'owner' ELSE 'editor' END
            FROM shared_wallet_members_legacy
            """
        )
        db.execute("DROP TABLE shared_wallet_members_legacy")
        db.execute("CREATE INDEX IF NOT EXISTS idx_wallet_members_user ON shared_wallet_members(user_id)")


def migrate_legacy_tables(db: sqlite3.Connection) -> None:
    """Archive the original pre-auth tables so they cannot be queried as live data."""
    for table in ("transactions", "budgets"):
        columns = {
            row["name"]
            for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if columns and "user_id" not in columns:
            legacy_name = f"{table}_legacy"
            db.execute(f"ALTER TABLE {table} RENAME TO {legacy_name}")
    transaction_columns = {
        row["name"]
        for row in db.execute("PRAGMA table_info(transactions)").fetchall()
    }
    additions = {
        "vendor": "TEXT NOT NULL DEFAULT ''",
        "payment_method": "TEXT NOT NULL DEFAULT 'cash'",
        "notes": "TEXT NOT NULL DEFAULT ''",
        "tags": "TEXT NOT NULL DEFAULT ''",
        "receipt_path": "TEXT",
    }
    for name, definition in additions.items():
        if transaction_columns and name not in transaction_columns:
            db.execute(f"ALTER TABLE transactions ADD COLUMN {name} {definition}")
    goods_columns = {
        row["name"]
        for row in db.execute("PRAGMA table_info(goods)").fetchall()
    }
    goods_additions = {
        "category": "TEXT NOT NULL DEFAULT 'General'",
        "purchase_date": "TEXT NOT NULL DEFAULT '1970-01-01'",
        "vendor": "TEXT NOT NULL DEFAULT ''",
        "warranty_expiry": "TEXT",
        "receipt_path": "TEXT",
        "photo_path": "TEXT",
        "notes": "TEXT NOT NULL DEFAULT ''",
        "estimated_value": "REAL NOT NULL DEFAULT 0",
    }
    for name, definition in goods_additions.items():
        if goods_columns and name not in goods_columns:
            db.execute(f"ALTER TABLE goods ADD COLUMN {name} {definition}")
    budget_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(budgets)").fetchall()
    }
    if budget_columns and "period" not in budget_columns:
        legacy_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(budgets_legacy)").fetchall()
        }
        if legacy_columns and "user_id" not in legacy_columns:
            db.execute("DROP TABLE budgets_legacy")
        db.execute("ALTER TABLE budgets RENAME TO budgets_legacy")
    recurring_columns = {
        row["name"]
        for row in db.execute("PRAGMA table_info(recurring_expenses)").fetchall()
    }
    for name, definition in {
        "category": "TEXT NOT NULL DEFAULT 'Bills'",
        "payment_method": "TEXT NOT NULL DEFAULT 'bank'",
        "status": "TEXT NOT NULL DEFAULT 'active'",
        "vendor": "TEXT NOT NULL DEFAULT ''",
        "notes": "TEXT NOT NULL DEFAULT ''",
        "price_history": "TEXT NOT NULL DEFAULT '[]'",
    }.items():
        if recurring_columns and name not in recurring_columns:
            db.execute(f"ALTER TABLE recurring_expenses ADD COLUMN {name} {definition}")
    savings_columns = {
        row["name"]
        for row in db.execute("PRAGMA table_info(savings_goals)").fetchall()
    }
    for name, definition in {
        "goal_type": "TEXT NOT NULL DEFAULT 'savings'",
        "monthly_contribution": "REAL NOT NULL DEFAULT 0",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    }.items():
        if savings_columns and name not in savings_columns:
            db.execute(f"ALTER TABLE savings_goals ADD COLUMN {name} {definition}")
