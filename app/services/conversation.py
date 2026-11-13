"""Shared message pipeline used by BOTH the demo simulator and real WhatsApp.

customer message -> save (with duplicate check) -> AI agent -> save reply
"""
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import utcnow
from app.models import Business, Conversation, Customer, Message
from app.services import agent as agent_service

logger = logging.getLogger(__name__)

NOT_CONFIGURED_REPLY = (
    "[Demo notice] The AI is not configured yet. Add OPENAI_API_KEY to your .env file "
    "and restart the server."
)
ERROR_REPLY = "Sorry, I'm having a technical problem right now. A team member will get back to you soon."


@dataclass
class HandleResult:
    conversation: Conversation | None
    reply: str | None
    duplicate: bool = False
    order_number: str | None = None


def get_or_create_customer(db: Session, business: Business, phone: str, name: str | None = None) -> Customer:
    customer = db.scalar(select(Customer).where(Customer.business_id == business.id, Customer.phone == phone))
    if customer is None:
        customer = Customer(business_id=business.id, phone=phone, name=(name or None))
        db.add(customer)
        db.flush()
    elif name and not customer.name:
        customer.name = name[:120]
    return customer


def get_or_create_conversation(db: Session, business: Business, customer: Customer, channel: str) -> Conversation:
    conversation = db.scalar(
        select(Conversation)
        .where(
            Conversation.business_id == business.id,
            Conversation.customer_id == customer.id,
            Conversation.channel == channel,
        )
        .order_by(Conversation.id.desc())
    )
    if conversation is None:
        conversation = Conversation(business_id=business.id, customer_id=customer.id, channel=channel)
        db.add(conversation)
        db.flush()
    return conversation


def add_message(db: Session, conversation: Conversation, role: str, content: str, external_id: str | None = None) -> Message:
    message = Message(
        conversation_id=conversation.id,
        business_id=conversation.business_id,
        role=role,
        content=content,
        external_id=external_id,
    )
    db.add(message)
    conversation.last_message_at = utcnow()
    db.flush()
    return message


def handle_incoming_message(
    db: Session,
    business: Business,
    *,
    phone: str,
    text: str,
    channel: str,
    external_id: str | None = None,
    profile_name: str | None = None,
    fixed_reply: str | None = None,
) -> HandleResult:
    """Process one customer message. If fixed_reply is given, the AI is skipped
    and that text is used as the reply (e.g. for unsupported message types)."""
    # 1) Duplicate check. Meta can deliver the same webhook more than once.
    if external_id and db.scalar(select(Message.id).where(Message.external_id == external_id)):
        logger.info("Duplicate message %s ignored", external_id)
        return HandleResult(conversation=None, reply=None, duplicate=True)

    # 2) Save the customer's message first and commit, so a parallel duplicate fails
    #    on the UNIQUE(external_id) constraint instead of being answered twice.
    try:
        customer = get_or_create_customer(db, business, phone, profile_name)
        conversation = get_or_create_conversation(db, business, customer, channel)
        customer_message = add_message(db, conversation, "customer", text.strip()[:4000], external_id)
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.info("Duplicate message %s ignored (race)", external_id)
        return HandleResult(conversation=None, reply=None, duplicate=True)

    # 3) A human has taken over: the AI stays quiet.
    if conversation.status == "human":
        return HandleResult(conversation=conversation, reply=None)

    if fixed_reply is not None:
        add_message(db, conversation, "assistant", fixed_reply)
        db.commit()
        return HandleResult(conversation=conversation, reply=fixed_reply)

    # 4) Ask the AI agent.
    order_number = None
    try:
        result = agent_service.run_agent(db, business, conversation, customer, customer_message)
        reply = result.reply
        order_number = result.order_number
    except agent_service.AgentNotConfigured:
        db.rollback()
        if channel == "simulator":
            reply = NOT_CONFIGURED_REPLY
        else:
            # Do not show configuration errors to real customers.
            conversation.status = "human"
            conversation.handoff_reason = "AI is not configured (missing OPENAI_API_KEY)."
            reply = (business.agent_settings.handoff_message if business.agent_settings else ERROR_REPLY)
    except Exception:
        logger.exception("Agent failed for conversation %s", conversation.id)
        db.rollback()
        conversation.status = "human"
        conversation.handoff_reason = "The AI agent raised an error. Please reply manually."
        reply = ERROR_REPLY

    role = "system" if reply == NOT_CONFIGURED_REPLY else "assistant"
    add_message(db, conversation, role, reply)
    db.commit()
    return HandleResult(conversation=conversation, reply=reply, order_number=order_number)
