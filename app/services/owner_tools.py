"""Tools for the OWNER assistant: the AI agent that manages the business dashboard.

The owner chats in plain language ("mark order 6 as shipped", "set earbuds
stock to 50", "how much did we sell this week?") and the LLM calls these
tools. Every tool is limited to the logged-in business.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import utcnow
from app.models import ORDER_STATUSES, Business, Conversation, Order, OrderItem, Product
from app.money import format_money, to_cents
from app.services.notifications import change_status_and_notify
from app.services.orders import OrderError

logger = logging.getLogger(__name__)


def _fn(name: str, description: str, properties: dict | None = None, required: list | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": required or []},
    }}


OWNER_TOOL_SCHEMAS = [
    _fn("get_sales_report", "Sales summary: number of orders, revenue, orders by status and top products.",
        {"period": {"type": "string", "enum": ["today", "last_7_days", "last_30_days", "all_time"]}}, ["period"]),
    _fn("list_orders", "List recent orders, optionally filtered by status.",
        {"status": {"type": "string", "enum": ORDER_STATUSES}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
    _fn("get_order_details", "Full details of one order.", {"order_number": {"type": "string"}}, ["order_number"]),
    _fn("update_order_status",
        "Change an order's status. By default the customer is sent an update message automatically.",
        {"order_number": {"type": "string"}, "status": {"type": "string", "enum": ORDER_STATUSES},
         "notify_customer": {"type": "boolean"}}, ["order_number", "status"]),
    _fn("find_products", "Search products (including hidden ones) with price and stock. Empty query lists all.",
        {"query": {"type": "string"}}),
    _fn("update_product",
        "Change a product. Only send the fields to change. Use add_stock to add/remove units, stock to set an exact number.",
        {"product_id": {"type": "integer"}, "name": {"type": "string"}, "price": {"type": "string"},
         "stock": {"type": "integer", "minimum": 0}, "add_stock": {"type": "integer"},
         "is_active": {"type": "boolean"}, "description": {"type": "string"}}, ["product_id"]),
    _fn("add_product", "Create a new product.",
        {"name": {"type": "string"}, "price": {"type": "string"}, "stock": {"type": "integer", "minimum": 0},
         "description": {"type": "string"}}, ["name", "price", "stock"]),
    _fn("low_stock_report", "Active products at or below a stock threshold.",
        {"threshold": {"type": "integer", "minimum": 0}}),
    _fn("conversations_needing_human", "Customer chats waiting for a human reply."),
]


@dataclass
class OwnerContext:
    db: Session
    business: Business
    actions: list[str] = field(default_factory=list)  # what was changed, shown to the owner

    def money(self, cents: int) -> str:
        return format_money(cents, self.business.currency)


def _order(ctx: OwnerContext, order_number) -> Order | None:
    digits = re.sub(r"\D", "", str(order_number or ""))
    if not digits:
        return None
    return ctx.db.scalar(select(Order).where(Order.id == int(digits), Order.business_id == ctx.business.id))


def _product(ctx: OwnerContext, product_id) -> Product | None:
    return ctx.db.scalar(select(Product).where(Product.id == int(product_id), Product.business_id == ctx.business.id))


def _order_row(ctx: OwnerContext, o: Order) -> dict:
    return {"order_number": o.number, "status": o.status, "customer": o.customer_name,
            "total": ctx.money(o.total_cents), "items": [f"{i.quantity} x {i.product_name}" for i in o.items],
            "created": o.created_at.strftime("%d %b %Y %H:%M")}


def _product_row(ctx: OwnerContext, p: Product) -> dict:
    return {"product_id": p.id, "name": p.name, "price": ctx.money(p.price_cents), "stock": p.stock,
            "active": p.is_active, "sku": p.sku}


# ----------------------------------------------------------------- tools
def get_sales_report(ctx: OwnerContext, period: str = "last_7_days") -> dict:
    now = utcnow()
    starts = {"today": now.replace(hour=0, minute=0, second=0, microsecond=0),
              "last_7_days": now - timedelta(days=7), "last_30_days": now - timedelta(days=30), "all_time": None}
    if period not in starts:
        return {"error": f"period must be one of {list(starts)}"}
    where = [Order.business_id == ctx.business.id]
    if starts[period] is not None:
        where.append(Order.created_at >= starts[period])

    by_status = dict(ctx.db.execute(select(Order.status, func.count()).where(*where).group_by(Order.status)).all())
    revenue = ctx.db.scalar(select(func.coalesce(func.sum(Order.total_cents), 0))
                            .where(*where, Order.status != "cancelled")) or 0
    top = ctx.db.execute(
        select(OrderItem.product_name, func.sum(OrderItem.quantity).label("qty"),
               func.sum(OrderItem.line_total_cents).label("sales"))
        .join(Order, Order.id == OrderItem.order_id)
        .where(*where, Order.status != "cancelled")
        .group_by(OrderItem.product_name).order_by(func.sum(OrderItem.line_total_cents).desc()).limit(5)
    ).all()
    return {
        "period": period,
        "orders": sum(by_status.values()),
        "revenue_excluding_cancelled": ctx.money(revenue),
        "orders_by_status": by_status,
        "top_products": [{"product": n, "units": int(q), "sales": ctx.money(int(s))} for n, q, s in top],
    }


def list_orders(ctx: OwnerContext, status: str | None = None, limit: int = 10) -> dict:
    stmt = select(Order).where(Order.business_id == ctx.business.id)
    if status:
        if status not in ORDER_STATUSES:
            return {"error": f"status must be one of {ORDER_STATUSES}"}
        stmt = stmt.where(Order.status == status)
    orders = ctx.db.scalars(stmt.order_by(Order.id.desc()).limit(max(1, min(int(limit or 10), 50)))).all()
    return {"count": len(orders), "orders": [_order_row(ctx, o) for o in orders]}


def get_order_details(ctx: OwnerContext, order_number: str) -> dict:
    order = _order(ctx, order_number)
    if order is None:
        return {"error": "Order not found."}
    return {**_order_row(ctx, order), "phone": order.phone, "delivery_address": order.delivery_address,
            "notes": order.notes, "source": order.source}


def update_order_status(ctx: OwnerContext, order_number: str, status: str, notify_customer: bool = True) -> dict:
    order = _order(ctx, order_number)
    if order is None:
        return {"error": "Order not found."}
    try:
        result = change_status_and_notify(ctx.db, ctx.business, order, status, notify=bool(notify_customer))
    except OrderError as exc:
        ctx.db.rollback()
        return {"error": str(exc)}
    if result.changed:
        ctx.actions.append(f"Order {order.number}: {result.old_status} → {result.new_status}")
    return {"order_number": order.number, "old_status": result.old_status, "new_status": result.new_status,
            "customer_notification": result.notification}


def find_products(ctx: OwnerContext, query: str = "") -> dict:
    stmt = select(Product).where(Product.business_id == ctx.business.id)
    words = [w for w in re.findall(r"[\w-]+", (query or "").lower()) if len(w) > 1]
    if words:
        stmt = stmt.where(or_(*[func.lower(Product.name).like(f"%{w}%") for w in words],
                              *[func.lower(func.coalesce(Product.sku, "")).like(f"%{w}%") for w in words]))
    products = ctx.db.scalars(stmt.order_by(Product.name).limit(30)).all()
    return {"count": len(products), "products": [_product_row(ctx, p) for p in products]}


def update_product(ctx: OwnerContext, product_id: int, name: str | None = None, price: str | None = None,
                   stock: int | None = None, add_stock: int | None = None, is_active: bool | None = None,
                   description: str | None = None) -> dict:
    product = _product(ctx, product_id)
    if product is None:
        return {"error": "Product not found."}
    changes = []
    try:
        if name is not None and name.strip():
            product.name = name.strip()[:120]
            changes.append(f"name → {product.name}")
        if description is not None:
            product.description = description.strip()[:2000]
            changes.append("description updated")
        if price is not None:
            product.price_cents = to_cents(price)
            changes.append(f"price → {ctx.money(product.price_cents)}")
        if stock is not None:
            if int(stock) < 0:
                return {"error": "Stock cannot be negative."}
            product.stock = int(stock)
            changes.append(f"stock → {product.stock}")
        if add_stock is not None:
            if product.stock + int(add_stock) < 0:
                return {"error": f"Only {product.stock} in stock; cannot remove {-int(add_stock)}."}
            product.stock += int(add_stock)
            changes.append(f"stock {int(add_stock):+d} → {product.stock}")
        if is_active is not None:
            product.is_active = bool(is_active)
            changes.append("visible to customers" if product.is_active else "hidden from customers")
    except ValueError as exc:
        ctx.db.rollback()
        return {"error": str(exc)}
    if not changes:
        return {"error": "Nothing to change. Tell me which field to update."}
    ctx.db.commit()
    ctx.actions.append(f"{product.name}: " + ", ".join(changes))
    return {"updated": _product_row(ctx, product), "changes": changes}


def add_product(ctx: OwnerContext, name: str, price: str, stock: int, description: str = "") -> dict:
    name = (name or "").strip()
    if not name:
        return {"error": "Product name is required."}
    try:
        price_cents = to_cents(price)
    except ValueError as exc:
        return {"error": str(exc)}
    if int(stock) < 0:
        return {"error": "Stock cannot be negative."}
    product = Product(business_id=ctx.business.id, name=name[:120], description=(description or "").strip()[:2000],
                      price_cents=price_cents, stock=int(stock))
    ctx.db.add(product)
    ctx.db.commit()
    ctx.actions.append(f"Added product {product.name} ({ctx.money(price_cents)}, stock {product.stock})")
    return {"created": _product_row(ctx, product)}


def low_stock_report(ctx: OwnerContext, threshold: int = 5) -> dict:
    products = ctx.db.scalars(
        select(Product).where(Product.business_id == ctx.business.id, Product.is_active.is_(True),
                              Product.stock <= int(threshold)).order_by(Product.stock)
    ).all()
    return {"threshold": threshold, "products": [_product_row(ctx, p) for p in products]}


def conversations_needing_human(ctx: OwnerContext) -> dict:
    convs = ctx.db.scalars(
        select(Conversation).where(Conversation.business_id == ctx.business.id, Conversation.status == "human")
        .order_by(Conversation.last_message_at.desc()).limit(20)
    ).all()
    return {"count": len(convs), "conversations": [
        {"conversation_id": c.id, "customer": c.customer.name or c.customer.phone, "reason": c.handoff_reason,
         "link": f"/conversations/{c.id}"} for c in convs]}


_OWNER_TOOLS = {
    "get_sales_report": get_sales_report,
    "list_orders": list_orders,
    "get_order_details": get_order_details,
    "update_order_status": update_order_status,
    "find_products": find_products,
    "update_product": update_product,
    "add_product": add_product,
    "low_stock_report": low_stock_report,
    "conversations_needing_human": conversations_needing_human,
}


def execute_owner_tool(ctx: OwnerContext, name: str, raw_arguments) -> dict:
    func_ = _OWNER_TOOLS.get(name)
    if func_ is None:
        return {"error": f"Unknown tool '{name}'."}
    try:
        args = raw_arguments if isinstance(raw_arguments, dict) else json.loads(raw_arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        return {"error": "Tool arguments were not valid JSON."}
    args = {k: v for k, v in args.items() if v is not None}
    try:
        result = func_(ctx, **args)
    except (TypeError, ValueError) as exc:
        logger.warning("Bad arguments for owner tool %s: %s", name, exc)
        return {"error": f"Invalid arguments for {name}."}
    except Exception:
        logger.exception("Owner tool %s failed", name)
        ctx.db.rollback()
        return {"error": "Internal error while running the tool."}
    logger.info("Owner tool %s(%s) -> %s", name, json.dumps(args)[:200], json.dumps(result, default=str)[:200])
    return result
