from datetime import date

import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "assistant.sqlite"),
        "SECRET_KEY": "assistant-test-key",
        "ENABLE_SCHEDULER": False,
    })


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client):
    with client.session_transaction() as session:
        return session["_csrf_token"]


def register_and_login(client, email):
    client.get("/register")
    token = csrf(client)
    client.post(
        "/register",
        data={
            "_csrf_token": token,
            "full_name": "Assistant User",
            "email": email,
            "password": "correct-password",
            "confirmation": "correct-password",
        },
    )
    client.get("/login")
    client.post(
        "/login",
        data={"_csrf_token": csrf(client), "email": email, "password": "correct-password"},
    )


def add_transaction(client, title, amount, kind="expense"):
    return client.post(
        "/transactions",
        data={
            "_csrf_token": csrf(client),
            "description": title,
            "amount": str(amount),
            "transaction_type": kind,
            "category": "Food",
            "transaction_date": date.today().isoformat(),
            "payment_method": "bank",
        },
    )


def test_assistant_answers_from_authenticated_user_records(client):
    register_and_login(client, "assistant@example.com")
    add_transaction(client, "Salary", 50000, "income")
    add_transaction(client, "Groceries", 1200)

    response = client.post(
        "/api/assistant/ask",
        json={"question": "How much did I spend this month?"},
        headers={"X-CSRFToken": csrf(client)},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["basis"] == "actual"
    assert payload["data"]["expenses"] == 1200
    assert "Rs. 1,200.00" in payload["answer"]


def test_assistant_affordability_is_explicitly_actual_and_user_scoped(client):
    register_and_login(client, "first@example.com")
    add_transaction(client, "Salary", 2000, "income")
    add_transaction(client, "Rent", 1000)

    response = client.post(
        "/api/assistant/ask",
        json={"question": "Can I afford an Rs. 1,200 purchase?"},
        headers={"X-CSRFToken": csrf(client)},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["kind"] == "affordability"
    assert payload["basis"] == "actual"
    assert payload["data"]["can_afford"] is False


def test_assistant_rejects_empty_and_unauthenticated_requests(client):
    assert client.post(
        "/api/assistant/ask",
        json={"question": "How much did I spend?"},
        headers={"X-CSRFToken": "missing"},
    ).status_code == 400

    register_and_login(client, "validation@example.com")
    response = client.post(
        "/api/assistant/ask",
        json={"question": ""},
        headers={"X-CSRFToken": csrf(client)},
    )
    assert response.status_code == 400
