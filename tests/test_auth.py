import sqlite3

import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.sqlite"), "SECRET_KEY": "test-key"})
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client):
    with client.session_transaction() as session:
        return session["_csrf_token"]


def register(client, email, name="Test User", password="correct-password"):
    client.get("/register")
    return client.post(
        "/register",
        data={"_csrf_token": csrf(client), "full_name": name, "email": email, "password": password, "confirmation": password},
        follow_redirects=True,
    )


def login(client, email, password="correct-password"):
    client.get("/login")
    return client.post(
        "/login",
        data={"_csrf_token": csrf(client), "email": email, "password": password},
        follow_redirects=True,
    )


def test_registration_hashes_password_and_login_works(client, app):
    response = register(client, "one@example.com")
    assert response.status_code == 200
    with sqlite3.connect(app.config["DATABASE"]) as db:
        password_hash = db.execute("SELECT password_hash FROM users").fetchone()[0]
    assert password_hash != "correct-password"
    assert password_hash.startswith("scrypt:")
    assert login(client, "one@example.com").status_code == 200
    assert b"Good morning" in login(client, "one@example.com").data


def test_duplicate_email_and_invalid_csrf_are_rejected(client):
    register(client, "one@example.com")
    duplicate = register(client, "ONE@example.com")
    assert b"already exists" in duplicate.data
    invalid = client.post("/login", data={"email": "one@example.com", "password": "correct-password"})
    assert invalid.status_code == 400


def test_users_cannot_see_each_others_records(client, app):
    register(client, "one@example.com")
    login(client, "one@example.com")
    with client.session_transaction() as session:
        first_user_id = session["user_id"]
    db = sqlite3.connect(app.config["DATABASE"])
    db.execute("INSERT INTO transactions (user_id, title, category, amount, transaction_type, transaction_date) VALUES (?, 'Private one', 'Test', 10, 'expense', '2026-01-01')", (first_user_id,))
    db.execute("INSERT INTO transactions (user_id, title, category, amount, transaction_type, transaction_date) VALUES (?, 'Private two', 'Test', 20, 'expense', '2026-01-01')", (first_user_id + 1,))
    db.commit()
    db.close()
    client.post("/logout", data={"_csrf_token": csrf(client)})
    register(client, "two@example.com", name="Second User")
    response = client.get("/")
    assert b"Private one" not in response.data
    assert b"Private two" not in response.data
