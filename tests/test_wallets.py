from datetime import date

import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "wallets.sqlite"),
        "SECRET_KEY": "wallet-test-key",
        "ENABLE_SCHEDULER": False,
    })


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client):
    with client.session_transaction() as session:
        return session["_csrf_token"]


def register(client, email):
    with client.session_transaction() as session:
        logged_in = "user_id" in session
    if logged_in:
        client.post("/logout", data={"_csrf_token": csrf(client)})
    client.get("/register")
    client.post("/register", data={
        "_csrf_token": csrf(client), "full_name": email.split("@")[0],
        "email": email, "password": "correct-password", "confirmation": "correct-password",
    })


def login(client, email):
    client.get("/login")
    client.post("/login", data={"_csrf_token": csrf(client), "email": email, "password": "correct-password"})


def test_wallet_equal_split_balances_and_settlement(client):
    register(client, "owner@example.com")
    register(client, "member@example.com")
    login(client, "owner@example.com")
    response = client.post("/wallets", data={"_csrf_token": csrf(client), "name": "Trip"})
    wallet_id = int(response.headers["Location"].rstrip("/").split("/")[-1])
    client.post("/wallets/%s/invite" % wallet_id, data={
        "_csrf_token": csrf(client), "email": "member@example.com", "role": "editor",
    })
    with client.application.app_context():
        from db import get_db
        member_id = get_db().execute("SELECT id FROM users WHERE email='member@example.com'").fetchone()["id"]
    response = client.post("/wallets/%s" % wallet_id, data={
        "_csrf_token": csrf(client), "title": "Dinner", "amount": "1000",
        "expense_date": date.today().isoformat(), "split_method": "equal",
        "member_ids": ["1", str(member_id)],
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"500.00" in response.data
    assert b"member owes owner" in response.data

    response = client.post("/wallets/%s/settle" % wallet_id, data={
        "_csrf_token": csrf(client), "from_user_id": str(member_id), "to_user_id": "1",
        "amount": "500", "settled_at": date.today().isoformat(),
    }, follow_redirects=True)
    assert b"Everyone is settled up" in response.data


def test_viewer_cannot_add_expense_or_manage_members(client):
    register(client, "owner2@example.com")
    register(client, "viewer@example.com")
    login(client, "owner2@example.com")
    response = client.post("/wallets", data={"_csrf_token": csrf(client), "name": "Home"})
    wallet_id = int(response.headers["Location"].rstrip("/").split("/")[-1])
    client.post("/wallets/%s/invite" % wallet_id, data={
        "_csrf_token": csrf(client), "email": "viewer@example.com", "role": "viewer",
    })
    client.post("/logout", data={"_csrf_token": csrf(client)})
    login(client, "viewer@example.com")
    assert client.get("/wallets/%s" % wallet_id).status_code == 200
    assert client.post("/wallets/%s" % wallet_id, data={
        "_csrf_token": csrf(client), "title": "Blocked", "amount": "10",
        "expense_date": date.today().isoformat(), "split_method": "equal", "member_ids": ["1"],
    }).status_code == 403
    assert client.post("/wallets/%s/invite" % wallet_id, data={
        "_csrf_token": csrf(client), "email": "owner2@example.com", "role": "editor",
    }).status_code == 403
