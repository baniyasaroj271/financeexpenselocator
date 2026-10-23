from __future__ import annotations

from datetime import date
import threading
import time

from db import get_db


def _next_due(current: date, frequency: str) -> date:
    from dateutil.relativedelta import relativedelta

    return current + {
        "weekly": relativedelta(weeks=1),
        "monthly": relativedelta(months=1),
        "quarterly": relativedelta(months=3),
        "yearly": relativedelta(years=1),
    }[frequency]


def process_recurring_payments(app, today: date | None = None) -> int:
    """Create due transactions once and register reminders atomically."""
    today = today or date.today()
    created = 0
    with app.app_context():
        db = get_db()
        recurring = db.execute(
            "SELECT * FROM recurring_expenses WHERE status = 'active' AND active = 1 AND next_due_date <= ?",
            (today.isoformat(),),
        ).fetchall()
        for payment in recurring:
            due = date.fromisoformat(payment["next_due_date"])
            while due <= today:
                run = db.execute(
                    "SELECT id FROM recurring_payment_runs WHERE recurring_id = ? AND due_date = ?",
                    (payment["id"], due.isoformat()),
                ).fetchone()
                if run is None:
                    cursor = db.execute(
                        """
                        INSERT INTO transactions
                        (user_id, title, category, amount, transaction_type, transaction_date,
                         vendor, payment_method, notes, tags)
                        VALUES (?, ?, ?, ?, 'expense', ?, ?, ?, ?, ?)
                        """,
                        (
                            payment["user_id"],
                            payment["title"],
                            payment["category"],
                            payment["amount"],
                            due.isoformat(),
                            payment["vendor"],
                            payment["payment_method"],
                            payment["notes"],
                            "recurring",
                        ),
                    )
                    db.execute(
                        """
                        INSERT INTO recurring_payment_runs
                        (recurring_id, user_id, due_date, transaction_id, reminder_sent)
                        VALUES (?, ?, ?, ?, 1)
                        """,
                        (payment["id"], payment["user_id"], due.isoformat(), cursor.lastrowid),
                    )
                    created += 1
                due = _next_due(due, payment["frequency"])
            db.execute(
                "UPDATE recurring_expenses SET next_due_date = ? WHERE id = ? AND status = 'active'",
                (due.isoformat(), payment["id"]),
            )
        db.commit()
    return created


def start_scheduler(app, interval_seconds: int = 3600) -> threading.Thread:
    def worker() -> None:
        while True:
            try:
                process_recurring_payments(app)
            except Exception:
                app.logger.exception("Recurring payment scheduler failed")
            time.sleep(interval_seconds)

    thread = threading.Thread(target=worker, name="hisabsmart-recurring", daemon=True)
    thread.start()
    return thread
