"""Order rules: database pricing, confirmation guard, stock handling."""
import pytest

from app.models import Conversation, Customer, Message, Product
from app.services.accounts import create_business
from app.services.orders import OrderError, create_order, update_order_status
from app.services.tools import ToolContext, execute_tool, is_affirmative


@pytest.fixture
def shop(db):
    business = create_business(db, name="Shop", email="shop@example.com", password="password123")
    product = Product(business_id=business.id, name="Earbuds", description="Wireless", price_cents=250000, stock=10)
    customer = Customer(business_id=business.id, phone="923001234567")
    db.add_all([product, customer])
    db.flush()
    conv = Conversation(business_id=business.id, customer_id=customer.id, channel="simulator")
    db.add(conv)
    db.flush()
    db.commit()
    return business, product, customer, conv


def _message(db, conv, text):
    m = Message(conversation_id=conv.id, business_id=conv.business_id, role="customer", content=text)
    db.add(m)
    db.flush()
    return m


def _ctx(db, business, conv, customer, msg):
    return ToolContext(db=db, business=business, conversation=conv, customer=customer,
                       latest_customer_message_id=msg.id, latest_customer_text=msg.content)


def test_affirmative_detection():
    assert is_affirmative("Yes please")
    assert is_affirmative("ok confirm")
    assert is_affirmative("haan ji")
    assert not is_affirmative("no")
    assert not is_affirmative("wait, change the address")
    assert not is_affirmative("what is the price?")


def test_totals_come_from_database(db, shop):
    business, product, customer, _ = shop
    order = create_order(db, business_id=business.id, customer=customer,
                         items=[{"product_id": product.id, "quantity": 3, "price": 1}],  # a fake price is ignored
                         customer_name="Umama Qayum Khan", delivery_address="House 5, Street 10, Islamabad")
    assert order.total_cents == 750000
    assert order.items[0].unit_price_cents == 250000
    assert product.stock == 7


def test_cannot_order_more_than_stock_or_other_business_products(db, shop):
    business, product, customer, _ = shop
    with pytest.raises(OrderError):
        create_order(db, business_id=business.id, customer=customer, items=[{"product_id": product.id, "quantity": 11}],
                     customer_name="Ali", delivery_address="Somewhere long enough")
    other = create_business(db, name="Other", email="other@example.com", password="password123")
    with pytest.raises(OrderError):
        create_order(db, business_id=other.id, customer=customer, items=[{"product_id": product.id, "quantity": 1}],
                     customer_name="Ali", delivery_address="Somewhere long enough")


def test_cancel_restores_stock(db, shop):
    business, product, customer, _ = shop
    order = create_order(db, business_id=business.id, customer=customer,
                         items=[{"product_id": product.id, "quantity": 4}],
                         customer_name="Ali", delivery_address="Somewhere long enough")
    assert product.stock == 6
    update_order_status(db, order, "cancelled")
    assert product.stock == 10
    update_order_status(db, order, "confirmed")
    assert product.stock == 6


def test_confirm_requires_a_later_yes_message(db, shop):
    business, product, customer, conv = shop
    m1 = _message(db, conv, "2 earbuds please. Umama Qayum Khan, House 5 Street 10 Islamabad")
    ctx = _ctx(db, business, conv, customer, m1)
    prepared = execute_tool(ctx, "prepare_order", {
        "items": [{"product_id": product.id, "quantity": 2}],
        "customer_name": "Umama Qayum Khan", "delivery_address": "House 5, Street 10, Islamabad"})
    assert prepared["status"] == "awaiting_customer_confirmation"
    assert "5,000.00" in prepared["summary"]

    # Same turn: the model tries to confirm before the customer has seen the summary.
    assert "error" in execute_tool(ctx, "confirm_order", {})

    # Next turn, but not a yes.
    m2 = _message(db, conv, "hmm what colours do you have?")
    assert "error" in execute_tool(_ctx(db, business, conv, customer, m2), "confirm_order", {})

    # Clear yes -> order placed.
    m3 = _message(db, conv, "Yes, confirm")
    ctx3 = _ctx(db, business, conv, customer, m3)
    result = execute_tool(ctx3, "confirm_order", {})
    assert result["status"] == "order_placed"
    assert ctx3.order_created.total_cents == 500000
    assert conv.pending_order is None
    assert product.stock == 8


def test_price_change_forces_reconfirmation(db, shop):
    business, product, customer, conv = shop
    m1 = _message(db, conv, "order")
    execute_tool(_ctx(db, business, conv, customer, m1), "prepare_order", {
        "items": [{"product_id": product.id, "quantity": 1}], "customer_name": "Umama Qayum Khan",
        "delivery_address": "House 5, Street 10, Islamabad"})
    product.price_cents = 300000  # business changes the price
    m2 = _message(db, conv, "yes")
    result = execute_tool(_ctx(db, business, conv, customer, m2), "confirm_order", {})
    assert "Prices changed" in result["error"]
    assert conv.pending_order["total_cents"] == 300000


def test_tool_errors_are_returned_not_raised(db, shop):
    business, _, customer, conv = shop
    m = _message(db, conv, "hi")
    ctx = _ctx(db, business, conv, customer, m)
    assert "error" in execute_tool(ctx, "does_not_exist", {})
    assert "error" in execute_tool(ctx, "get_product_details", "{not json")
    assert "error" in execute_tool(ctx, "get_product_details", {"wrong_arg": 1})
    assert "error" in execute_tool(ctx, "prepare_order", {"items": [], "customer_name": "Umama Qayum Khan",
                                                          "delivery_address": "Somewhere long"})


def test_search_products(db, shop):
    business, _, customer, conv = shop
    ctx = _ctx(db, business, conv, customer, _message(db, conv, "hi"))
    found = execute_tool(ctx, "search_products", {"query": "earbuds"})
    assert found["products"][0]["name"] == "Earbuds"
    fallback = execute_tool(ctx, "search_products", {"query": "laptop"})
    assert "No exact match" in fallback["note"]
