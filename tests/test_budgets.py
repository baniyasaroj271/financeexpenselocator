import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path, monkeypatch):
    import routes

    monkeypatch.setattr(routes, "date", __import__("datetime").date)
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "budgets.sqlite"),
            "SECRET_KEY": "budget-test-key",
        }
    )


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client):
    with client.session_transaction() as session:
        return session["_csrf_token"]


def login_user(client, email):
    client.get("/register")
    client.post(
        "/register",
        data={"_csrf_token": csrf(client), "full_name": "Budget Owner", "email": email,
              "password": "correct-password", "confirmation": "correct-password"},
    )
    client.get("/login")
    client.post(
        "/login",
        data={"_csrf_token": csrf(client), "email": email, "password": "correct-password"},
    )


def add_transaction(client, title, amount, category, transaction_date, kind="expense"):
    client.post(
        "/transactions",
        data={
            "_csrf_token": csrf(client),
            "description": title,
            "amount": str(amount),
            "transaction_type": kind,
            "category": category,
            "transaction_date": transaction_date,
            "payment_method": "bank",
        },
    )


def test_budget_uses_current_period_transactions_and_alerts(client, app, monkeypatch):
    login_user(client, "budget@example.com")
    import routes
    from datetime import date

    monkeypatch.setattr(routes, "date", date)
    add_transaction(client, "Rent", 450, "Housing", "2026-10-05")
    add_transaction(client, "Groceries", 100, "Food", "2026-10-06")
    response = client.post(
        "/budgets",
        data={
            "_csrf_token": csrf(client),
            "category": "Housing",
            "period": "monthly",
            "limit_amount": "500",
            "rollover_enabled": "on",
            "rollover_amount": "25",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"450.00" in response.data
    assert b"healthy" in response.data
    with app.app_context():
        from db import get_db

        row = get_db().execute("SELECT period, rollover_enabled FROM budgets").fetchone()
        assert row["period"] == "monthly"
        assert row["rollover_enabled"] == 1


def test_savings_emergency_goal_and_upcoming_bill_are_persisted(client):
    login_user(client, "goals@example.com")
    response = client.post(
        "/savings",
        data={
            "_csrf_token": csrf(client),
            "name": "Emergency reserve",
            "goal_type": "emergency",
            "target_amount": "10000",
            "current_amount": "2500",
            "monthly_contribution": "500",
            "target_date": "2027-10-01",
        },
        follow_redirects=True,
    )
    assert b"Emergency reserve" in response.data
    response = client.post(
        "/bills",
        data={
            "_csrf_token": csrf(client),
            "title": "Internet",
            "amount": "40",
            "frequency": "monthly",
            "next_due_date": "2026-10-30",
            "category": "Bills",
        },
        follow_redirects=True,
    )
    assert b"Internet" in response.data


def test_budget_and_goal_records_are_user_scoped(client):
    login_user(client, "first-budget@example.com")
    client.post(
        "/budgets",
        data={"_csrf_token": csrf(client), "category": "Private", "period": "weekly", "limit_amount": "100"},
    )
    client.post("/logout", data={"_csrf_token": csrf(client)})
    login_user(client, "second-budget@example.com")
    response = client.get("/budgets")
    assert b"Private" not in response.data
