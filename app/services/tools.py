"""Tools the AI agent can call (OpenAI function/tool calling).

The LLM decides WHICH tool to call and with WHAT arguments. Our Python code
does the real work against the database and returns JSON. This keeps facts
(prices, stock, orders) coming from the database, not from the model.
"""
import json
import logging
import re
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Business, Conversation, Customer, Order, Product
from app.money import format_money
from app.services.orders import OrderError, create_order, price_items

logger = logging.getLogger(__name__)

# ------------------------------------------------------------ tool schemas
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": (
                "Search the store catalog by keywords. Returns product IDs, prices and stock. "
                "Use an empty query to list all available products. Always call this before "
                "talking about a product's price or availability."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords, e.g. 'earbuds' or 'black t-shirt'."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_details",
            "description": "Get full details for one product by its ID.",
            "parameters": {
                "type": "object",
                "properties": {"product_id": {"type": "integer"}},
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prepare_order",
            "description": (
                "Create an order SUMMARY for the customer to check. Call this only after you know "
                "the products, quantities, the customer's full name and full delivery address. "
                "This does NOT place the order. Show the returned summary to the customer and ask "
                "them to reply YES to confirm."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "product_id": {"type": "integer"},
                                "quantity": {"type": "integer", "minimum": 1},
                            },
                            "required": ["product_id", "quantity"],
                        },
                    },
                    "customer_name": {"type": "string"},
                    "delivery_address": {"type": "string"},
                    "notes": {"type": "string", "description": "Optional delivery notes."},
                },
                "required": ["items", "customer_name", "delivery_address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_order",
            "description": (
                "Place the prepared order. Call this ONLY when the customer's latest message clearly "
                "confirms the summary you showed them (e.g. 'yes', 'confirm')."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_pending_order",
            "description": "Discard the prepared (not yet confirmed) order, e.g. when the customer changes their mind.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": "Look up the status of one of THIS customer's orders by order number (e.g. 12 or '#00012').",
            "parameters": {
                "type": "object",
                "properties": {"order_number": {"type": "string"}},
                "required": ["order_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transfer_to_human",
            "description": (
                "Hand the conversation to a human team member. Use when the customer asks for a human, "
                "is angry, has a complaint, refund or payment problem, or when you cannot help."
            ),
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
]

_AFFIRMATIVE = re.compile(
    r"\b(yes|yeah|yep|yup|ya|confirm|confirmed|ok|okay|sure|proceed|correct|go ahead|place it|"
    r"place the order|do it|haan|han|ji|jee|theek|done)\b|👍|✅",
    re.IGNORECASE,
)
_NEGATIVE = re.compile(r"\b(no|not|don'?t|cancel|wait|change|stop|wrong)\b", re.IGNORECASE)


def is_affirmative(text: str) -> bool:
    return bool(_AFFIRMATIVE.search(text or "")) and not _NEGATIVE.search(text or "")


@dataclass
class ToolContext:
    db: Session
    business: Business
    conversation: Conversation
    customer: Customer
    latest_customer_message_id: int
    latest_customer_text: str
    order_created: Order | None = None
    handed_off: bool = False


# ---------------------------------------------------------------- helpers
def _product_dict(p: Product, currency: str) -> dict:
    return {
        "product_id": p.id,
        "name": p.name,
        "description": (p.description or "")[:300],
        "price": format_money(p.price_cents, currency),
        "in_stock": p.stock > 0,
        "stock": p.stock,
    }


def _summary_text(draft: dict, currency: str) -> str:
    lines = [
        f"- {item['quantity']} x {item['name']} @ {format_money(item['unit_price_cents'], currency)}"
        f" = {format_money(item['line_total_cents'], currency)}"
        for item in draft["items"]
    ]
    return (
        "\n".join(lines)
        + f"\nTotal: {format_money(draft['total_cents'], currency)}"
        + f"\nName: {draft['customer_name']}\nDelivery address: {draft['delivery_address']}"
    )


# ------------------------------------------------------------ tool bodies
def search_products(ctx: ToolContext, query: str = "") -> dict:
    stmt = select(Product).where(Product.business_id == ctx.business.id, Product.is_active.is_(True))
    words = [w for w in re.findall(r"[\w-]+", (query or "").lower()) if len(w) > 1]
    if words:
        conditions = []
        for w in words:
            like = f"%{w}%"
            conditions += [func.lower(Product.name).like(like), func.lower(Product.description).like(like)]
        matches = ctx.db.scalars(stmt.where(or_(*conditions)).order_by(Product.name).limit(10)).all()
        if matches:
            return {"products": [_product_dict(p, ctx.business.currency) for p in matches]}
    everything = ctx.db.scalars(stmt.order_by(Product.name).limit(25)).all()
    return {
        "note": "No exact match. Here is the full catalog." if words else "Full catalog.",
        "products": [_product_dict(p, ctx.business.currency) for p in everything],
    }


def get_product_details(ctx: ToolContext, product_id: int) -> dict:
    product = ctx.db.scalar(
        select(Product).where(Product.id == int(product_id), Product.business_id == ctx.business.id)
    )
    if product is None or not product.is_active:
        return {"error": "Product not found."}
    return _product_dict(product, ctx.business.currency)


def prepare_order(
    ctx: ToolContext, items: list, customer_name: str, delivery_address: str, notes: str = ""
) -> dict:
    customer_name = (customer_name or "").strip()
    delivery_address = (delivery_address or "").strip()
    if len(customer_name) < 2:
        return {"error": "Ask the customer for their full name first."}
    if len(delivery_address) < 5:
        return {"error": "Ask the customer for their full delivery address first."}
    try:
        lines = price_items(ctx.db, ctx.business.id, items or [])
    except OrderError as exc:
        return {"error": str(exc)}

    draft = {
        "items": [line.to_dict() for line in lines],
        "total_cents": sum(line.line_total_cents for line in lines),
        "customer_name": customer_name[:120],
        "delivery_address": delivery_address,
        "notes": (notes or "").strip()[:500],
    }
    ctx.conversation.pending_order = draft
    # Remember which customer message this summary answers. Confirmation must come later.
    ctx.conversation.pending_order_message_id = ctx.latest_customer_message_id
    ctx.customer.name = customer_name[:120]
    ctx.customer.address = delivery_address

    return {
        "status": "awaiting_customer_confirmation",
        "summary": _summary_text(draft, ctx.business.currency),
        "instruction": "Show this exact summary to the customer and ask them to reply YES to confirm.",
    }


def confirm_order(ctx: ToolContext) -> dict:
    conv = ctx.conversation
    draft = conv.pending_order
    if not draft:
        return {"error": "There is no prepared order. Collect the details and call prepare_order first."}

    # Guard 1: the confirmation must be a NEW customer message, sent after the summary.
    if conv.pending_order_message_id is not None and ctx.latest_customer_message_id <= conv.pending_order_message_id:
        return {"error": "The customer has not seen and confirmed the summary yet. Show it and ask for YES."}
    # Guard 2: the latest customer message must actually say yes.
    if not is_affirmative(ctx.latest_customer_text):
        return {"error": "The customer's latest message is not a clear confirmation. Ask them to reply YES."}

    items = [{"product_id": i["product_id"], "quantity": i["quantity"]} for i in draft["items"]]

    # Guard 3: re-price from the database. If prices changed, show a fresh summary.
    try:
        fresh_lines = price_items(ctx.db, ctx.business.id, items)
    except OrderError as exc:
        conv.pending_order = None
        conv.pending_order_message_id = None
        return {"error": f"Could not place the order: {exc}"}
    fresh_total = sum(line.line_total_cents for line in fresh_lines)
    if fresh_total != draft["total_cents"]:
        draft = {**draft, "items": [line.to_dict() for line in fresh_lines], "total_cents": fresh_total}
        conv.pending_order = draft
        conv.pending_order_message_id = ctx.latest_customer_message_id
        return {
            "error": "Prices changed since the summary. Show this updated summary and ask again.",
            "summary": _summary_text(draft, ctx.business.currency),
        }

    try:
        order = create_order(
            ctx.db,
            business_id=ctx.business.id,
            customer=ctx.customer,
            items=items,
            customer_name=draft["customer_name"],
            delivery_address=draft["delivery_address"],
            notes=draft.get("notes", ""),
            conversation_id=conv.id,
            source=conv.channel,
        )
    except OrderError as exc:
        return {"error": f"Could not place the order: {exc}"}

    conv.pending_order = None
    conv.pending_order_message_id = None
    ctx.order_created = order
    return {
        "status": "order_placed",
        "order_number": order.number,
        "total": format_money(order.total_cents, ctx.business.currency),
        "message": "Tell the customer the order number and that the team will process it soon.",
    }


def cancel_pending_order(ctx: ToolContext) -> dict:
    had_draft = bool(ctx.conversation.pending_order)
    ctx.conversation.pending_order = None
    ctx.conversation.pending_order_message_id = None
    return {"status": "cancelled" if had_draft else "nothing_to_cancel"}


def get_order_status(ctx: ToolContext, order_number: str) -> dict:
    digits = re.sub(r"\D", "", str(order_number or ""))
    if not digits:
        return {"error": "Please provide a valid order number."}
    order = ctx.db.scalar(
        select(Order).where(
            Order.id == int(digits),
            Order.business_id == ctx.business.id,
            Order.customer_id == ctx.customer.id,  # customers only see their own orders
        )
    )
    if order is None:
        return {"error": "No order with that number was found for this customer."}
    return {
        "order_number": order.number,
        "status": order.status,
        "total": format_money(order.total_cents, ctx.business.currency),
        "items": [f"{i.quantity} x {i.product_name}" for i in order.items],
    }


def transfer_to_human(ctx: ToolContext, reason: str = "") -> dict:
    ctx.conversation.status = "human"
    ctx.conversation.handoff_reason = (reason or "Customer needs a human").strip()[:500]
    ctx.handed_off = True
    logger.info("Conversation %s handed to a human: %s", ctx.conversation.id, ctx.conversation.handoff_reason)
    return {"status": "transferred", "message": "Tell the customer a team member will reply shortly."}


_TOOLS = {
    "search_products": search_products,
    "get_product_details": get_product_details,
    "prepare_order": prepare_order,
    "confirm_order": confirm_order,
    "cancel_pending_order": cancel_pending_order,
    "get_order_status": get_order_status,
    "transfer_to_human": transfer_to_human,
}


def execute_tool(ctx: ToolContext, name: str, raw_arguments: str | dict | None) -> dict:
    """Run one tool safely. Errors are returned as JSON so the model can recover."""
    func_ = _TOOLS.get(name)
    if func_ is None:
        return {"error": f"Unknown tool '{name}'."}
    try:
        args = raw_arguments if isinstance(raw_arguments, dict) else json.loads(raw_arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
    except (json.JSONDecodeError, ValueError):
        return {"error": "Tool arguments were not valid JSON."}
    try:
        result = func_(ctx, **args)
    except TypeError as exc:
        logger.warning("Bad arguments for tool %s: %s", name, exc)
        return {"error": f"Invalid arguments for {name}."}
    except Exception:  # never let one tool crash the whole conversation
        logger.exception("Tool %s failed", name)
        return {"error": "Internal error while running the tool."}
    logger.info("Tool %s(%s) -> %s", name, json.dumps(args)[:200], json.dumps(result)[:200])
    return result
