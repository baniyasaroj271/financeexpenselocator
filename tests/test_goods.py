import io

import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "goods.sqlite"),
            "SECRET_KEY": "goods-test-key",
            "GOODS_UPLOAD_FOLDER": str(tmp_path / "goods-uploads"),
            "UPLOAD_FOLDER": str(tmp_path / "receipt-uploads"),
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
        data={"_csrf_token": csrf(client), "full_name": "Goods Owner", "email": email,
              "password": "correct-password", "confirmation": "correct-password"},
    )
    client.get("/login")
    client.post(
        "/login",
        data={"_csrf_token": csrf(client), "email": email, "password": "correct-password"},
    )


def good_data(client, **overrides):
    values = {
        "_csrf_token": csrf(client),
        "name": "Desk lamp",
        "category": "Furniture",
        "purchase_price": "80.00",
        "quantity": "2",
        "purchase_date": "2026-10-01",
        "vendor": "Light Co",
        "warranty_expiry": "2027-10-01",
        "estimated_value": "55.00",
        "notes": "Warm light",
    }
    values.update(overrides)
    return values


def test_goods_crud_search_and_uploads(client):
    login_user(client, "goods@example.com")
    response = client.post(
        "/goods",
        data={
            **good_data(client),
            "photo": (io.BytesIO(b"\x89PNG\r\n\x1a\nfake"), "lamp.png"),
            "receipt": (io.BytesIO(b"%PDF-1.7 fake"), "receipt.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Desk lamp" in response.data
    assert b"Purchase value" in response.data
    assert b"Desk lamp" in client.get("/goods?q=Light").data
    detail = client.get("/goods/1")
    assert detail.status_code == 200
    assert b"Warm light" in detail.data
    assert client.get("/goods/1/file/photo").status_code == 200
    assert client.get("/goods/1/file/receipt").status_code == 200
    response = client.post(
        "/goods/1/edit",
        data=good_data(client, name="Updated lamp"),
        follow_redirects=True,
    )
    assert b"Updated lamp" in response.data
    response = client.post(
        "/goods/1/delete",
        data={"_csrf_token": csrf(client)},
        follow_redirects=True,
    )
    assert b"Updated lamp" not in response.data


def test_goods_ownership_and_upload_validation(client):
    login_user(client, "first-goods@example.com")
    client.post("/goods", data=good_data(client))
    client.post("/logout", data={"_csrf_token": csrf(client)})
    login_user(client, "second-goods@example.com")
    assert client.get("/goods/1").status_code == 404
    assert client.get("/goods/1/file/photo").status_code == 404
    assert client.post("/goods/1/delete", data={"_csrf_token": csrf(client)}).status_code == 404
    invalid = client.post(
        "/goods",
        data={**good_data(client), "photo": (io.BytesIO(b"not image"), "photo.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"valid image" in invalid.data
