"""Business accounts and sample data."""
import random
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import utcnow
from app.models import AgentSettings, Business, Conversation, Customer, Message, Order, Product, WhatsAppConfig
from app.money import to_cents
from app.security import hash_password, new_token
from app.services.orders import create_order

DEFAULT_INSTRUCTIONS = (
    "Greet customers warmly. Help them find products, answer questions about price and "
    "availability, and take orders. Suggest a similar product if something is out of stock."
)

SAMPLE_PRODUCTS = [
    ("Wireless Earbuds Pro", "Bluetooth 5.3 earbuds, noise cancellation, 30h battery with case.", "EAR-PRO", "4999", 25),
    ("Smart Watch S2", "Fitness tracking, heart-rate monitor, 7-day battery, waterproof.", "WATCH-S2", "8999", 12),
    ("Power Bank 20000mAh", "Fast charging power bank with USB-C and two USB-A ports.", "PB-20K", "3499", 40),
    ("USB-C Fast Charger 30W", "Compact wall charger for phones and tablets.", "CHG-30W", "1799", 60),
    ("Phone Case (Black)", "Shockproof silicone case, fits most 6.1 inch phones.", "CASE-BLK", "899", 3),
    ("Laptop Backpack", "Water-resistant backpack with padded 15.6 inch laptop pocket.", "BAG-LT", "5499", 8),
    ("Bluetooth Speaker Mini", "Portable speaker, 12h playtime, deep bass.", "SPK-MINI", "2999", 0),
    ("Mechanical Keyboard", "RGB backlit mechanical keyboard with blue switches.", "KB-MECH", "7499", 15),
]

SAMPLE_BUSINESS_INFO = """Store: online electronics and accessories shop.
Delivery: all over Pakistan in 2-4 working days. Delivery fee is included in prices.
Payment: Cash on Delivery.
Returns: 7-day return for unused items in original packaging.
Working hours: Monday to Saturday, 10am to 8pm."""


def create_business(db: Session, *, name: str, email: str, password: str, currency: str = "PKR") -> Business:
    business = Business(name=name, email=email.lower().strip(), password_hash=hash_password(password), currency=currency)
    business.agent_settings = AgentSettings(agent_name="RidesAI", instructions=DEFAULT_INSTRUCTIONS, business_info="")
    business.whatsapp_config = WhatsAppConfig(webhook_key=new_token(18), verify_token=new_token(12))
    db.add(business)
    db.flush()
    return business


def add_sample_products(db: Session, business: Business) -> list[Product]:
    products = [
        Product(business_id=business.id, name=n, description=d, sku=s, price_cents=to_cents(p), stock=st)
        for n, d, s, p, st in SAMPLE_PRODUCTS
    ]
    db.add_all(products)
    if business.agent_settings and not business.agent_settings.business_info:
        business.agent_settings.business_info = SAMPLE_BUSINESS_INFO
    db.flush()
    return products


def add_sample_activity(db: Session, business: Business, seed: int = 7) -> None:
    """Fake customers, chats and orders over the last week so the dashboard has data."""
    rng = random.Random(seed)
    products = db.scalars(
        select(Product).where(Product.business_id == business.id, Product.stock > 5)
    ).all()
    people = [
        ("923001112233", "Umama Qayum Khan", "House 5, Street 10, G-11/2, Islamabad"),
        ("923214445566", "Wisha Qayum Khan", "Flat 3B, Gulberg III, Lahore"),
        ("923335556677", "Asma Qayum Khan", "45-C, DHA Phase 5, Karachi"),
        ("923457778899", "Waqas Qayum Khan", "Street 9, Satellite Town, Rawalpindi"),
        ("923129990011", "Waqar Qayum Khan", "House 7, Model Town, Lahore"),
    ]
    now = utcnow()
    statuses = ["delivered", "delivered", "shipped", "confirmed", "pending", "pending"]
    for i, (phone, name, address) in enumerate(people):
        customer = Customer(business_id=business.id, phone=phone, name=name, address=address)
        db.add(customer)
        db.flush()
        conv = Conversation(business_id=business.id, customer_id=customer.id, channel="simulator")
        db.add(conv)
        db.flush()
        product = rng.choice(products)
        qty = rng.randint(1, 2)
        db.add_all([
            Message(conversation_id=conv.id, business_id=business.id, role="customer",
                    content=f"Hi, is {product.name} available?"),
            Message(conversation_id=conv.id, business_id=business.id, role="assistant",
                    content=f"Yes! {product.name} is in stock. Would you like to order?"),
        ])
        order = create_order(
            db, business_id=business.id, customer=customer,
            items=[{"product_id": product.id, "quantity": qty}],
            customer_name=name, delivery_address=address, conversation_id=conv.id,
        )
        order.status = statuses[i % len(statuses)]
        order.created_at = now - timedelta(days=rng.randint(0, 6), hours=rng.randint(0, 10))
        conv.last_message_at = order.created_at
    # One conversation waiting for a human
    conv = db.scalar(select(Conversation).where(Conversation.business_id == business.id).order_by(Conversation.id.desc()))
    if conv:
        conv.status = "human"
        conv.handoff_reason = "Customer asked about a refund."
        db.add(Message(conversation_id=conv.id, business_id=business.id, role="customer",
                       content="I want a refund for my last order."))
    db.flush()


def get_business_by_email(db: Session, email: str) -> Business | None:
    return db.scalar(select(Business).where(Business.email == email.lower().strip()))
