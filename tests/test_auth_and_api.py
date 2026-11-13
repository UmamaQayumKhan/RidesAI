"""Auth, CSRF, product/order API and data isolation between businesses."""
from fastapi.testclient import TestClient

from app.main import app
from app.security import decrypt_secret, encrypt_secret, hash_password, verify_password
from tests.conftest import csrf_from, register


def test_password_hashing():
    hashed = hash_password("s3cret-pass")
    assert hashed != "s3cret-pass"
    assert verify_password("s3cret-pass", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("anything", "garbage")


def test_secret_encryption_roundtrip():
    enc = encrypt_secret("EAAG-token")
    assert enc and "EAAG" not in enc
    assert decrypt_secret(enc) == "EAAG-token"
    assert decrypt_secret("not-valid") == ""


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_dashboard_requires_login(client):
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/api/products").status_code == 401


def test_register_login_logout(client):
    token = register(client)
    assert client.get("/dashboard").status_code == 200
    client.post("/logout", data={"csrf_token": token})
    assert client.get("/dashboard", follow_redirects=False).status_code == 303

    token = csrf_from(client.get("/login").text)
    bad = client.post("/login", data={"email": "owner@shop-example.com", "password": "nope", "csrf_token": token})
    assert bad.status_code == 400
    ok = client.post("/login", data={"email": "owner@shop-example.com", "password": "password123", "csrf_token": token},
                     follow_redirects=False)
    assert ok.status_code == 303


def test_register_validation_and_duplicate(client):
    token = csrf_from(client.get("/register").text)
    short = client.post("/register", data={"business_name": "Shop", "email": "a@example.com",
                                           "password": "short", "csrf_token": token})
    assert short.status_code == 400
    register(client, email="dup@shop-example.com")
    client.cookies.clear()
    token = csrf_from(client.get("/register").text)
    dup = client.post("/register", data={"business_name": "Shop 2", "email": "dup@shop-example.com",
                                         "password": "password123", "csrf_token": token})
    assert dup.status_code == 400 and "already exists" in dup.text


def test_csrf_is_enforced(client):
    register(client)
    r = client.post("/api/products", json={"name": "X", "price": "10", "stock": 1})
    assert r.status_code == 403


def test_product_api_create_and_validate(client):
    token = register(client)
    headers = {"X-CSRF-Token": token}
    r = client.post("/api/products", json={"name": "Mug", "price": "499.50", "stock": 10}, headers=headers)
    assert r.status_code == 201
    assert r.json()["price_cents"] == 49950
    bad = client.post("/api/products", json={"name": "Mug", "price": "-1", "stock": 10}, headers=headers)
    assert bad.status_code == 422
    assert [p["name"] for p in client.get("/api/products").json()] == ["Mug"]


def test_business_data_is_isolated():
    with TestClient(app) as a, TestClient(app) as b:
        token_a = register(a, email="a@shop-example.com", name="Shop A")
        register(b, email="b@shop-example.com", name="Shop B")
        pid = a.post("/api/products", json={"name": "Secret item", "price": "100", "stock": 5},
                     headers={"X-CSRF-Token": token_a}).json()["id"]

        assert b.get("/api/products").json() == []
        token_b = csrf_from(b.get("/products").text)
        assert b.put(f"/api/products/{pid}", json={"name": "Hacked", "price": "1", "stock": 1},
                     headers={"X-CSRF-Token": token_b}).status_code == 404
        assert b.post(f"/products/{pid}/delete", data={"csrf_token": token_b}).status_code == 404
        assert a.get("/api/products").json()[0]["name"] == "Secret item"


def test_pages_render_with_sample_data(client):
    register(client, sample=True)
    for path in ["/dashboard", "/products", "/orders", "/customers", "/conversations", "/settings", "/simulator"]:
        r = client.get(path)
        assert r.status_code == 200, path
    orders = client.get("/api/orders").json()
    assert len(orders) == 5
    assert client.get(f"/orders/{orders[0]['id']}").status_code == 200
    stats = client.get("/api/stats").json()
    assert stats["orders"] == 5 and stats["customers"] == 5


def test_settings_and_human_reply(client, fake_llm):
    token = register(client)
    # Live mode cannot be switched on with missing credentials.
    client.post("/settings/whatsapp", data={"phone_number_id": "123", "is_active": "true", "csrf_token": token})
    assert "OFF" in client.get("/settings").text
    client.post("/settings/whatsapp", data={"phone_number_id": "123", "access_token": "EAAG-secret-token",
                                            "app_secret": "app-secret", "verify_token": "vt",
                                            "is_active": "true", "csrf_token": token})
    page = client.get("/settings").text
    assert "LIVE" in page and "EAAG-secret-token" not in page  # secret is masked
    client.post("/settings/agent", data={"business_name": "New Name", "currency": "usd", "agent_name": "Max",
                                         "instructions": "Be brief", "business_info": "Open 9-5",
                                         "handoff_message": "", "csrf_token": token})
    assert client.get("/api/me").json()["currency"] == "USD"

    fake_llm([("text", "Hello!")])
    conv_id = client.post("/api/simulator/message", headers={"X-CSRF-Token": token},
                          json={"phone": "923004444444", "message": "hi"}).json()["conversation_id"]
    client.post(f"/conversations/{conv_id}/reply", data={"message": "Hi, this is a real person.", "csrf_token": token})
    history = client.get("/api/simulator/messages", params={"phone": "923004444444"}).json()
    assert history["conversation_status"] == "human"
    assert history["messages"][-1]["role"] == "human_agent"
    client.post(f"/conversations/{conv_id}/status", data={"status": "ai", "csrf_token": token})
    assert client.get("/api/simulator/messages", params={"phone": "923004444444"}).json()["conversation_status"] == "ai"
