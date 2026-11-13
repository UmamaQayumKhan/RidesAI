"""Order logic: pricing, stock checks, creation and status changes.

Important rule: totals are ALWAYS calculated here from database prices.
The AI only sends product IDs and quantities, never prices.
"""
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ORDER_STATUSES, Customer, Order, OrderItem, Product

logger = logging.getLogger(__name__)

MAX_QUANTITY_PER_ITEM = 100
MAX_ITEMS_PER_ORDER = 20


class OrderError(ValueError):
    """A business-rule problem that can be explained to the user/customer."""


@dataclass
class PricedLine:
    product_id: int
    name: str
    unit_price_cents: int
    quantity: int

    @property
    def line_total_cents(self) -> int:
        return self.unit_price_cents * self.quantity

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "name": self.name,
            "unit_price_cents": self.unit_price_cents,
            "quantity": self.quantity,
            "line_total_cents": self.line_total_cents,
        }


def price_items(db: Session, business_id: int, items: list[dict]) -> list[PricedLine]:
    """Validate requested items and price them from the database.

    `items` is a list of {"product_id": int, "quantity": int}.
    Duplicate product IDs are merged.
    """
    if not items:
        raise OrderError("The order has no items.")
    if len(items) > MAX_ITEMS_PER_ORDER:
        raise OrderError(f"An order can have at most {MAX_ITEMS_PER_ORDER} different items.")

    merged: dict[int, int] = {}
    for raw in items:
        try:
            product_id = int(raw["product_id"])
            quantity = int(raw["quantity"])
        except (KeyError, TypeError, ValueError) as exc:
            raise OrderError("Each item needs a numeric product_id and quantity.") from exc
        if quantity < 1:
            raise OrderError("Quantity must be at least 1.")
        merged[product_id] = merged.get(product_id, 0) + quantity

    lines: list[PricedLine] = []
    for product_id, quantity in merged.items():
        if quantity > MAX_QUANTITY_PER_ITEM:
            raise OrderError(f"Maximum {MAX_QUANTITY_PER_ITEM} units per product in one order.")
        # business_id filter = a customer can never order another business's product
        product = db.scalar(
            select(Product).where(Product.id == product_id, Product.business_id == business_id)
        )
        if product is None or not product.is_active:
            raise OrderError(f"Product {product_id} was not found.")
        if product.stock < quantity:
            raise OrderError(
                f"Only {product.stock} unit(s) of '{product.name}' are in stock, "
                f"but {quantity} were requested."
            )
        lines.append(PricedLine(product.id, product.name, product.price_cents, quantity))
    return lines


def create_order(
    db: Session,
    *,
    business_id: int,
    customer: Customer,
    items: list[dict],
    customer_name: str,
    delivery_address: str,
    notes: str = "",
    conversation_id: int | None = None,
    source: str = "simulator",
) -> Order:
    """Create an order and reduce stock. Prices are re-read from the database."""
    customer_name = (customer_name or "").strip()
    delivery_address = (delivery_address or "").strip()
    if len(customer_name) < 2:
        raise OrderError("Customer name is required.")
    if len(delivery_address) < 5:
        raise OrderError("A full delivery address is required.")

    lines = price_items(db, business_id, items)
    order = Order(
        business_id=business_id,
        customer_id=customer.id,
        conversation_id=conversation_id,
        status="pending",
        customer_name=customer_name[:120],
        phone=customer.phone,
        delivery_address=delivery_address,
        notes=(notes or "").strip()[:1000],
        total_cents=sum(line.line_total_cents for line in lines),
        source=source,
    )
    for line in lines:
        order.items.append(
            OrderItem(
                product_id=line.product_id,
                product_name=line.name,
                unit_price_cents=line.unit_price_cents,
                quantity=line.quantity,
                line_total_cents=line.line_total_cents,
            )
        )
        product = db.get(Product, line.product_id)
        product.stock -= line.quantity

    db.add(order)
    db.flush()
    logger.info("Order %s created for business %s (total=%s)", order.id, business_id, order.total_cents)
    return order


def update_order_status(db: Session, order: Order, new_status: str) -> Order:
    """Change status. Cancelling returns stock; un-cancelling takes it again."""
    if new_status not in ORDER_STATUSES:
        raise OrderError(f"Unknown status '{new_status}'.")
    old_status = order.status
    if old_status == new_status:
        return order

    if new_status == "cancelled":
        for item in order.items:
            if item.product_id:
                product = db.get(Product, item.product_id)
                if product:
                    product.stock += item.quantity
    elif old_status == "cancelled":
        for item in order.items:
            if item.product_id:
                product = db.get(Product, item.product_id)
                if product is None:
                    continue
                if product.stock < item.quantity:
                    raise OrderError(f"Not enough stock of '{product.name}' to re-open this order.")
                product.stock -= item.quantity

    order.status = new_status
    logger.info("Order %s status %s -> %s", order.id, old_status, new_status)
    return order
