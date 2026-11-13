"""End-to-end MVP flow through the simulator API with a scripted fake LLM:
customer message -> AI agent -> product lookup -> confirmation -> order -> dashboard.
"""
from tests.conftest import last_tool_result, register


def _first_product_id(messages):
    return last_tool_result(messages)["products"][0]["product_id"]


def test_full_order_flow(client, fake_llm):
    token = register(client, sample=True)
    headers = {"X-CSRF-Token": token}
    before = len(client.get("/api/orders").json())
    earbuds = next(p for p in client.get("/api/products").json() if p["name"] == "Wireless Earbuds Pro")

    # Turn 1: customer asks and gives details; AI searches, prepares the order, asks to confirm.
    fake_llm([
        ("tool", "search_products", {"query": "earbuds"}),
        ("tool", "prepare_order", lambda msgs: {
            "items": [{"product_id": _first_product_id(msgs), "quantity": 2}],
            "customer_name": "Umama Qayum Khan",
            "delivery_address": "House 5, Street 10, G-11/2, Islamabad",
        }),
        ("text", "Here is your order summary. Reply YES to confirm."),
    ])
    r = client.post("/api/simulator/message", headers=headers, json={
        "phone": "+92 300 7654321", "message": "I want 2 earbuds. Umama Qayum Khan, House 5, Street 10, G-11/2, Islamabad"})
    assert r.status_code == 200, r.text
    assert "YES" in r.json()["reply"]
    assert len(client.get("/api/orders").json()) == before  # nothing placed yet

    # Turn 2: customer says yes; AI confirms.
    fake_llm([("tool", "confirm_order", {}), ("text", "Your order is placed!")])
    r = client.post("/api/simulator/message", headers=headers, json={"phone": "923007654321", "message": "yes"})
    assert r.status_code == 200
    assert r.json()["reply"] == "Your order is placed!"

    orders = client.get("/api/orders").json()
    assert len(orders) == before + 1
    new = orders[0]
    assert new["customer_name"] == "Umama Qayum Khan"
    assert new["total_cents"] == earbuds["price_cents"] * 2  # from the database, not the LLM
    assert new["status"] == "pending"
    stock_after = next(p for p in client.get("/api/products").json() if p["id"] == earbuds["id"])["stock"]
    assert stock_after == earbuds["stock"] - 2
    assert "Umama Qayum Khan" in client.get("/orders").text  # visible on the dashboard


def test_handoff_to_human_silences_ai(client, fake_llm):
    token = register(client)
    headers = {"X-CSRF-Token": token}
    fake_llm([("tool", "transfer_to_human", {"reason": "refund request"}), ("text", "A team member will reply.")])
    r = client.post("/api/simulator/message", headers=headers, json={"phone": "923001111111", "message": "I want a refund"})
    assert r.json()["conversation_status"] == "human"

    llm = fake_llm([])  # AI must NOT be called while a human is handling the chat
    r = client.post("/api/simulator/message", headers=headers, json={"phone": "923001111111", "message": "hello?"})
    assert r.json()["reply"] is None
    assert llm.calls == []


def test_agent_not_configured_message(client):
    token = register(client)
    r = client.post("/api/simulator/message", headers={"X-CSRF-Token": token},
                    json={"phone": "923001111111", "message": "hi"})
    assert r.status_code == 200
    assert "not configured" in r.json()["reply"]


def test_tool_round_limit_hands_off(client, fake_llm):
    token = register(client)
    fake_llm([("tool", "search_products", {"query": ""})] * 10)
    r = client.post("/api/simulator/message", headers={"X-CSRF-Token": token},
                    json={"phone": "923002222222", "message": "loop forever"})
    assert r.json()["conversation_status"] == "human"


def test_system_prompt_contains_business_settings(client, fake_llm):
    token = register(client, sample=True)
    llm = fake_llm([("text", "Hello!")])
    client.post("/api/simulator/message", headers={"X-CSRF-Token": token},
                json={"phone": "923003333333", "message": "hi"})
    system = llm.calls[0]["messages"][0]["content"]
    assert "RidesAI" in system and "Cash on Delivery" in system
    assert {t["function"]["name"] for t in llm.calls[0]["tools"]} >= {"search_products", "prepare_order", "confirm_order"}
