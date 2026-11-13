"""Automatic customer updates when an order's status changes.

Used by the dashboard, the REST API and the owner AI assistant, so the
customer is always told, no matter where the change came from.
"""
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Business, Conversation, Order
from app.services import whatsapp
from app.services.conversation import add_message
from app.services.orders import update_order_status

logger = logging.getLogger(__name__)

STATUS_MESSAGES = {
    "confirmed": "Good news, {name}! Your order {number} is confirmed and we are preparing it now.",
    "shipped": "Your order {number} has been shipped 🚚 It is on the way to: {address}",
    "delivered": "Your order {number} has been delivered. Thank you for shopping with {business}! 😊",
    "cancelled": "Your order {number} has been cancelled. If this is a mistake, just reply to this message.",
    "pending": "Your order {number} is back to pending. We will update you soon.",
}


@dataclass
class StatusChange:
    changed: bool
    old_status: str
    new_status: str
    notification: str  # human readable result, shown to the business


def status_message(business: Business, order: Order) -> str:
    template = STATUS_MESSAGES.get(order.status, "Your order {number} is now {status}.")
    return template.format(
        name=order.customer_name.split()[0] if order.customer_name else "there",
        number=order.number,
        address=order.delivery_address,
        business=business.name,
        status=order.status,
    )


def _conversation_for(db: Session, order: Order) -> Conversation | None:
    if order.conversation_id:
        conv = db.get(Conversation, order.conversation_id)
        if conv is not None:
            return conv
    # Fall back to the customer's latest chat (prefer live WhatsApp).
    return db.scalar(
        select(Conversation)
        .where(Conversation.business_id == order.business_id, Conversation.customer_id == order.customer_id)
        .order_by((Conversation.channel == "whatsapp").desc(), Conversation.last_message_at.desc())
    )


def change_status_and_notify(
    db: Session, business: Business, order: Order, new_status: str, notify: bool = True
) -> StatusChange:
    """Update the status (stock rules apply), commit, then message the customer.

    Raises OrderError if the change is not allowed (nothing is saved then).
    """
    old_status = order.status
    update_order_status(db, order, new_status)
    if old_status == order.status:
        return StatusChange(False, old_status, order.status, "Status unchanged, so no message was sent.")

    if not notify:
        db.commit()
        return StatusChange(True, old_status, order.status, "Customer was not notified.")

    conv = _conversation_for(db, order)
    if conv is None:
        db.commit()
        return StatusChange(True, old_status, order.status, "No chat found for this customer, so no message was sent.")

    text = status_message(business, order)
    add_message(db, conv, "assistant", text)
    db.commit()  # save first, then send: never message the customer about a change that was not saved

    if conv.channel == "whatsapp":
        config = business.whatsapp_config
        if config is not None and whatsapp.send_text(config, conv.customer.phone, text):
            note = "Customer notified on WhatsApp."
        else:
            note = "Status saved, but the WhatsApp message failed. Check WhatsApp settings."
    else:
        note = "Customer notified in the demo chat (simulator)."
    logger.info("Order %s: %s -> %s. %s", order.id, old_status, order.status, note)
    return StatusChange(True, old_status, order.status, note)
