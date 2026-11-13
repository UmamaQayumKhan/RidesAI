"""DEMO MODE: a fake WhatsApp chat in the browser.

It uses exactly the same pipeline as real WhatsApp (services/conversation.py),
but nothing is sent to Meta. Conversations are marked channel="simulator".
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_business
from app.models import Business, Conversation, Customer, Message
from app.schemas import ChatMessageOut, SimulatorMessageIn, SimulatorReplyOut, SimulatorResetIn
from app.security import require_csrf_header
from app.services.conversation import handle_incoming_message
from app.templating import render

router = APIRouter(tags=["simulator"])


def _find_conversation(db: Session, business: Business, phone: str) -> Conversation | None:
    customer = db.scalar(select(Customer).where(Customer.business_id == business.id, Customer.phone == phone))
    if customer is None:
        return None
    return db.scalar(
        select(Conversation)
        .where(Conversation.business_id == business.id, Conversation.customer_id == customer.id,
               Conversation.channel == "simulator")
        .order_by(Conversation.id.desc())
    )


def _messages_out(db: Session, conv: Conversation | None) -> list[ChatMessageOut]:
    if conv is None:
        return []
    rows = db.scalars(select(Message).where(Message.conversation_id == conv.id).order_by(Message.id)).all()
    return [ChatMessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at) for m in rows]


@router.get("/simulator")
def simulator_page(request: Request, business: Business = Depends(get_current_business)):
    return render(request, "simulator.html", {"business": business, "active": "simulator",
                                              "llm_configured": get_settings().llm_configured})


@router.post("/api/simulator/message", response_model=SimulatorReplyOut, dependencies=[Depends(require_csrf_header)])
def simulator_message(data: SimulatorMessageIn, business: Business = Depends(get_current_business),
                      db: Session = Depends(get_db)):
    result = handle_incoming_message(db, business, phone=data.phone, text=data.message,
                                     channel="simulator", profile_name=data.name)
    conv = result.conversation
    return SimulatorReplyOut(
        conversation_id=conv.id,
        conversation_status=conv.status,
        reply=result.reply,
        messages=_messages_out(db, conv),
    )


@router.get("/api/simulator/messages", response_model=SimulatorReplyOut)
def simulator_history(phone: str, business: Business = Depends(get_current_business), db: Session = Depends(get_db)):
    phone = "".join(ch for ch in phone if ch.isdigit())
    conv = _find_conversation(db, business, phone)
    return SimulatorReplyOut(
        conversation_id=conv.id if conv else 0,
        conversation_status=conv.status if conv else "ai",
        reply=None,
        messages=_messages_out(db, conv),
    )


@router.post("/api/simulator/reset", dependencies=[Depends(require_csrf_header)])
def simulator_reset(data: SimulatorResetIn, business: Business = Depends(get_current_business),
                    db: Session = Depends(get_db)):
    """Start a fresh demo chat. Orders already placed are kept."""
    conv = _find_conversation(db, business, data.phone)
    if conv is not None:
        db.delete(conv)
        db.commit()
    return {"status": "reset"}
