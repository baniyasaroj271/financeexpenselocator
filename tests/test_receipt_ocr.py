import io
import json

import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path, monkeypatch):
    import routes

    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "ocr.sqlite"),
            "SECRET_KEY": "ocr-test-key",
            "OCR_UPLOAD_FOLDER": str(tmp_path / "ocr-uploads"),
        }
    )
    monkeypatch.setattr(
        routes,
        "extract_receipt",
        lambda _bytes: {
            "vendor": "Fresh Market",
            "date": "2026-10-20",
            "subtotal": 18.0,
            "tax": 1.8,
            "total": 19.8,
            "items": [{"name": "Apples", "quantity": 2, "price": 4.5}],
            "warnings": [],
            "suggested_category": "Food & Dining",
        },
    )
    return app


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
        data={
            "_csrf_token": csrf(client),
            "full_name": "OCR User",
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


def test_scan_review_and_save_selected_goods(client, app):
    login_user(client, "ocr@example.com")
    response = client.post(
        "/receipts/scan",
        data={"_csrf_token": csrf(client), "receipt": (io.BytesIO(b"image bytes"), "receipt.jpg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    assert "/review" in response.location
    review = client.get(response.location)
    assert review.status_code == 200
    assert b"Fresh Market" in review.data
    response = client.post(
        response.location,
        data={
            "_csrf_token": csrf(client),
            "vendor": "Fresh Market",
            "transaction_date": "2026-10-20",
            "category": "Food & Dining",
            "total": "19.80",
            "payment_method": "card",
            "items_json": json.dumps([{"selected": "1", "name": "Apples", "quantity": "2", "price": "4.50"}]),
        },
        follow_redirects=True,
    )
    assert b"Fresh Market" in response.data
    with app.app_context():
        from db import get_db

        assert get_db().execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
        assert get_db().execute("SELECT COUNT(*) FROM goods").fetchone()[0] == 1
        assert get_db().execute("SELECT status FROM receipt_scans").fetchone()[0] == "saved"


def test_duplicate_receipt_and_duplicate_transaction_are_blocked(client):
    login_user(client, "duplicate@example.com")
    payload = {"_csrf_token": csrf(client), "receipt": (io.BytesIO(b"same image"), "receipt.jpg")}
    first = client.post("/receipts/scan", data=payload, content_type="multipart/form-data")
    review_url = first.location
    data = {
        "_csrf_token": csrf(client),
        "vendor": "Fresh Market",
        "transaction_date": "2026-10-20",
        "category": "Food & Dining",
        "total": "19.80",
        "payment_method": "card",
        "items_json": "[]",
    }
    client.post(review_url, data=data)
    second = client.post(
        "/receipts/scan",
        data={"_csrf_token": csrf(client), "receipt": (io.BytesIO(b"same image"), "receipt.jpg")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"already saved" in second.data
    client.get("/receipts/scan")
    with client.session_transaction() as session:
        token = session["_csrf_token"]
    response = client.post(
        "/receipts/scan",
        data={"_csrf_token": token, "receipt": (io.BytesIO(b"different image"), "receipt.jpg")},
        content_type="multipart/form-data",
    )
    duplicate_review = response.location
    blocked = client.post(duplicate_review, data=data, follow_redirects=True)
    assert b"matching transaction already exists" in blocked.data


def test_scan_isolated_between_users(client, app):
    login_user(client, "first-ocr@example.com")
    response = client.post(
        "/receipts/scan",
        data={"_csrf_token": csrf(client), "receipt": (io.BytesIO(b"private"), "receipt.jpg")},
        content_type="multipart/form-data",
    )
    scan_url = response.location
    scan_id = scan_url.split("/")[-2]
    client.post("/logout", data={"_csrf_token": csrf(client)})
    login_user(client, "second-ocr@example.com")
    assert client.get(f"/receipts/scan/{scan_id}/review").status_code == 404
    assert client.get(f"/receipts/scan/{scan_id}/file").status_code == 404
