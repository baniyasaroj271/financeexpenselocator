from datetime import date, timedelta

import pytest

from app import create_app
from scheduler import process_recurring_payments


@pytest.fixture()
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "reports.sqlite"),
        "SECRET_KEY": "reports-test-key",
        "ENABLE_SCHEDULER": False,
    })


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client):
    with client.session_transaction() as session:
        return session["_csrf_token"]


def login_user(client, email):
    client.get("/register")
    client.post("/register", data={
        "_csrf_token": csrf(client), "full_name": "Reports User", "email": email,
        "password": "correct-password", "confirmation": "correct-password",
    })
    client.get("/login")
    client.post("/login", data={
        "_csrf_token": csrf(client), "email": email, "password": "correct-password",
    })


def add_transaction(client, title, amount, kind="expense"):
    client.post("/transactions", data={
        "_csrf_token": csrf(client), "description": title, "amount": str(amount),
        "transaction_type": kind, "category": "Food", "transaction_date": date.today().isoformat(),
        "payment_method": "bank",
    })


def test_report_exports_are_available_and_user_scoped(client):
    login_user(client, "reports@example.com")
    add_transaction(client, "Private expense", 123)
    for export_format, content_type in (
        ("csv", "text/csv"),
        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("pdf", "application/pdf"),
    ):
        response = client.get(f"/reports/export/{export_format}")
        assert response.status_code == 200
        assert response.content_type.startswith(content_type)
        assert response.data
    assert client.get("/reports/export/xml").status_code == 400


def test_recurring_scheduler_is_idempotent(app, client):
    login_user(client, "recurring@example.com")
    due_date = (date.today() - timedelta(days=1)).isoformat()
    response = client.post("/recurring", data={
        "_csrf_token": csrf(client), "title": "Internet", "amount": "800",
        "frequency": "monthly", "next_due_date": due_date, "category": "Bills",
        "payment_method": "bank",
    })
    assert response.status_code == 302
    first = process_recurring_payments(app, date.today())
    second = process_recurring_payments(app, date.today())
    assert first == 1
    assert second == 0
    with app.app_context():
        from db import get_db
        assert get_db().execute("SELECT COUNT(*) count FROM recurring_payment_runs").fetchone()["count"] == 1
        assert get_db().execute("SELECT COUNT(*) count FROM transactions WHERE tags='recurring'").fetchone()["count"] == 1
