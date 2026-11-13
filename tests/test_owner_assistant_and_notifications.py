"""Order status notifications + the owner AI assistant that manages the dashboard."""
from fastapi.testclient import TestClient

from app.main import app
from app.services import whatsapp
from tests.conftest import last_tool_result, register


def _place_order(client, token, fake_llm, phone="923001112299"):
    """Create one order through the simulator with a scripted agent."""
    fake_llm([
        ("tool", "search_products", {"query": "earbuds"}),
        ("tool", "prepare_order", lambda msgs: {
            "items": [{"product_id": last_tool_result(msgs)["products"][0]["product_id"], "quantity": 1}],
            "customer_name": "Umama Qayum Khan", "delivery_address": "House 5, Street 10, G-11/2, Islamabad"}),
        ("text", "Reply YES to confirm."),
    ])
    h = {"X-CSRF-Token": token}
    client.post("/api/simulator/message", headers=h, json={"phone": phone, "message": "1 earbuds, Umama, G-11"})
    fake_llm([("tool", "confirm_order", {}), ("text", "Placed!")])
    client.post("/api/simulator/message", headers=h, json={"phone": phone, "message": "yes"})
    return client.get("/api/orders").json()[0]


# ------------------------------------------------------- notifications
def test_status_change_notifies_customer_in_chat(client, fake_llm):
    token = register(client, sample=True)
    order = _place_order(client, token, fake_llm)

    r = client.patch(f"/api/orders/{order['id']}/status", json={"status": "shipped"}, headers={"X-CSRF-Token": token})
    assert r.status_code == 200 and r.json()["status"] == "shipped"
    chat = client.get("/api/simulator/messages", params={"phone": "923001112299"}).json()["messages"]
    assert "has been shipped" in chat[-1]["content"] and order["number"] in chat[-1]["content"]

    # Same status again: no duplicate message.
    client.patch(f"/api/orders/{order['id']}/status", json={"status": "shipped"}, headers={"X-CSRF-Token": token})
    assert len(client.get("/api/simulator/messages", params={"phone": "923001112299"}).json()["messages"]) == len(chat)

    # Opt out of notification.
    client.patch(f"/api/orders/{order['id']}/status?notify=false", json={"status": "delivered"},
                 headers={"X-CSRF-Token": token})
    assert len(client.get("/api/simulator/messages", params={"phone": "923001112299"}).json()["messages"]) == len(chat)


def test_dashboard_form_status_change_notifies(client, fake_llm):
    token = register(client, sample=True)
    order = _place_order(client, token, fake_llm)
    r = client.post(f"/orders/{order['id']}/status", data={"status": "confirmed", "csrf_token": token})
    assert r.status_code == 200 and "notified" in r.text
    chat = client.get("/api/simulator/messages", params={"phone": "923001112299"}).json()["messages"]
    assert "is confirmed" in chat[-1]["content"]


def test_whatsapp_order_notification_is_sent(client, fake_llm, monkeypatch, db):
    from sqlalchemy import select

    from app.models import Conversation, Order, WhatsAppConfig
    from app.security import encrypt_secret

    token = register(client, sample=True)
    order = _place_order(client, token, fake_llm)
    # Pretend this conversation came from live WhatsApp.
    o = db.get(Order, order["id"])
    conv = db.get(Conversation, o.conversation_id)
    conv.channel = "whatsapp"
    wa = db.scalar(select(WhatsAppConfig).where(WhatsAppConfig.business_id == o.business_id))
    wa.phone_number_id, wa.access_token_enc, wa.is_active = "123", encrypt_secret("tok"), True
    db.commit()

    sent = []
    monkeypatch.setattr(whatsapp, "send_text", lambda c, to, body: sent.append((to, body)) or True)
    client.patch(f"/api/orders/{order['id']}/status", json={"status": "shipped"}, headers={"X-CSRF-Token": token})
    assert sent and sent[0][0] == "923001112299" and "shipped" in sent[0][1]


# ------------------------------------------------------ owner assistant
def test_owner_assistant_updates_order_and_stock(client, fake_llm):
    token = register(client, sample=True)
    h = {"X-CSRF-Token": token}
    order = _place_order(client, token, fake_llm)
    case = next(p for p in client.get("/api/products").json() if p["name"] == "Phone Case (Black)")

    fake_llm([
        ("tool", "update_order_status", {"order_number": order["number"], "status": "shipped"}),
        ("text", f"Order {order['number']} is now shipped and the customer was notified."),
    ])
    r = client.post("/api/assistant/message", headers=h, json={"message": f"Mark order {order['number']} as shipped"})
    assert r.status_code == 200
    assert r.json()["actions"] == [f"Order {order['number']}: pending → shipped"]
    assert client.get(f"/api/orders/{order['id']}").json()["status"] == "shipped"
    chat = client.get("/api/simulator/messages", params={"phone": "923001112299"}).json()["messages"]
    assert "shipped" in chat[-1]["content"]  # customer was told automatically

    fake_llm([
        ("tool", "find_products", {"query": "phone case"}),
        ("tool", "update_product", lambda msgs: {"product_id": last_tool_result(msgs)["products"][0]["product_id"],
                                                 "add_stock": 20}),
        ("text", "Added 20 units."),
    ])
    r = client.post("/api/assistant/message", headers=h, json={"message": "Add 20 units to the phone case"})
    assert any("stock +20" in a for a in r.json()["actions"])
    after = next(p for p in client.get("/api/products").json() if p["id"] == case["id"])
    assert after["stock"] == case["stock"] + 20


def test_owner_assistant_report_and_add_product(client, fake_llm):
    token = register(client, sample=True)
    h = {"X-CSRF-Token": token}
    llm = fake_llm([
        ("tool", "get_sales_report", {"period": "all_time"}),
        ("text", "Here is your report."),
    ])
    client.post("/api/assistant/message", headers=h, json={"message": "How are sales?"})
    report = last_tool_result(llm.calls[-1]["messages"])
    assert report["orders"] == 5 and report["top_products"]

    fake_llm([("tool", "add_product", {"name": "Wireless Mouse", "price": "1999", "stock": 30}), ("text", "Added.")])
    client.post("/api/assistant/message", headers=h, json={"message": "Add Wireless Mouse 1999 stock 30"})
    mouse = next(p for p in client.get("/api/products").json() if p["name"] == "Wireless Mouse")
    assert mouse["price_cents"] == 199900 and mouse["stock"] == 30
    history = client.get("/api/assistant/messages").json()["messages"]
    assert [m["role"] for m in history] == ["owner", "assistant", "owner", "assistant"]
    client.post("/api/assistant/clear", headers=h, json={})
    assert client.get("/api/assistant/messages").json()["messages"] == []
    assert client.get("/assistant").status_code == 200


def test_owner_assistant_cannot_touch_other_business(fake_llm):
    with TestClient(app) as a, TestClient(app) as b:
        token_a = register(a, email="a@shop-example.com", name="Shop A")
        pid = a.post("/api/products", json={"name": "A item", "price": "100", "stock": 5},
                     headers={"X-CSRF-Token": token_a}).json()["id"]
        token_b = register(b, email="b@shop-example.com", name="Shop B")
        llm = fake_llm([("tool", "update_product", {"product_id": pid, "stock": 0}), ("text", "ok")])
        b.post("/api/assistant/message", headers={"X-CSRF-Token": token_b}, json={"message": "set stock 0"})
        assert last_tool_result(llm.calls[-1]["messages"]) == {"error": "Product not found."}
        assert a.get("/api/products").json()[0]["stock"] == 5


def test_owner_assistant_not_configured(client):
    token = register(client)
    r = client.post("/api/assistant/message", headers={"X-CSRF-Token": token}, json={"message": "hi"})
    assert "not configured" in r.json()["reply"]
