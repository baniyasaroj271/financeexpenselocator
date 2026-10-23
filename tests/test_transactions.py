import io

import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "transactions.sqlite"),
            "SECRET_KEY": "transaction-test-key",
            "UPLOAD_FOLDER": str(tmp_path / "receipts"),
        }
    )


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client):
    with client.session_transaction() as session:
        return session["_csrf_token"]


def create_user(client, email, name="Test User"):
    client.get("/register")
    client.post(
        "/register",
        data={
            "_csrf_token": csrf(client),
            "full_name": name,
            "email": email,
            "password": "correct-password",
            "confirmation": "correct-password",
        },
    )
    client.get("/login")
    client.post(
        "/login",
        data={
            "_csrf_token": csrf(client),
            "email": email,
            "password": "correct-password",
        },
    )


def transaction_data(client, **overrides):
    values = {
        "_csrf_token": csrf(client),
        "description": "Office supplies",
        "amount": "125.50",
        "transaction_type": "expense",
        "category": "Work",
        "vendor": "Acme",
        "transaction_date": "2026-10-20",
        "payment_method": "card",
        "tags": "business, reimbursable",
        "notes": "Quarterly supplies",
    }
    values.update(overrides)
    return values


def test_transaction_create_search_edit_and_delete(client):
    create_user(client, "owner@example.com")
    response = client.post(
        "/transactions",
        data={
            **transaction_data(client),
            "receipt": (io.BytesIO(b"%PDF fake receipt"), "receipt.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Office supplies" in response.data
    assert b"reimbursable" in response.data
    assert client.get("/transactions?q=Acme").status_code == 200
    assert b"Office supplies" in client.get("/transactions?q=Acme").data

    with client.application.app_context():
        from db import get_db

        transaction_id = get_db().execute("SELECT id FROM transactions").fetchone()["id"]
    response = client.post(
        f"/transactions/{transaction_id}/edit",
        data=transaction_data(client, description="Updated supplies", amount="200"),
        follow_redirects=True,
    )
    assert b"Updated supplies" in response.data
    response = client.post(
        f"/transactions/{transaction_id}/delete",
        data={"_csrf_token": csrf(client)},
        follow_redirects=True,
    )
    assert b"Updated supplies" not in response.data


def test_transaction_validation_and_owner_checks(client):
    create_user(client, "first@example.com")
    invalid = client.post(
        "/transactions",
        data=transaction_data(client, amount="-1"),
        follow_redirects=True,
    )
    assert b"Amount must be greater than zero" in invalid.data
    client.post("/logout", data={"_csrf_token": csrf(client)})
    create_user(client, "second@example.com", "Second User")
    assert client.get("/transactions/1/edit").status_code == 404
    assert client.post(
        "/transactions/1/delete",
        data={"_csrf_token": csrf(client)},
    ).status_code == 404


def test_invalid_receipt_extension_is_rejected(client):
    create_user(client, "receipt@example.com")
    response = client.post(
        "/transactions",
        data={
            **transaction_data(client),
            "receipt": (io.BytesIO(b"not an executable"), "receipt.exe"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Receipt must be a PDF" in response.data
