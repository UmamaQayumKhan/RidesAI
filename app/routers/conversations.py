"""Customer conversations: read chats, reply as a human, hand back to the AI."""
import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_business
from app.models import Business, Conversation, Message
from app.security import verify_csrf
from app.services import whatsapp
from app.services.conversation import add_message
from app.templating import flash, render

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/conversations", tags=["conversations"])


def get_owned_conversation(db: Session, business: Business, conversation_id: int) -> Conversation:
    conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id,
                                                Conversation.business_id == business.id))
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.get("")
def list_conversations(request: Request, filter: str = "", business: Business = Depends(get_current_business),
                       db: Session = Depends(get_db)):
    last_text = (
        select(Message.content).where(Message.conversation_id == Conversation.id)
        .order_by(Message.id.desc()).limit(1).correlate(Conversation).scalar_subquery()
    )
    msg_count = (
        select(func.count(Message.id)).where(Message.conversation_id == Conversation.id)
        .correlate(Conversation).scalar_subquery()
    )
    stmt = select(Conversation, last_text, msg_count).where(Conversation.business_id == business.id)
    if filter == "human":
        stmt = stmt.where(Conversation.status == "human")
    rows = db.execute(stmt.order_by(Conversation.last_message_at.desc()).limit(300)).all()
    return render(request, "conversations.html", {"business": business, "active": "conversations",
                                                  "rows": rows, "filter": filter})


@router.get("/{conversation_id}")
def conversation_detail(request: Request, conversation_id: int, business: Business = Depends(get_current_business),
                        db: Session = Depends(get_db)):
    conv = get_owned_conversation(db, business, conversation_id)
    return render(request, "conversation_detail.html", {"business": business, "active": "conversations",
                                                        "conv": conv})


@router.post("/{conversation_id}/reply")
def human_reply(request: Request, conversation_id: int, message: str = Form(...), csrf_token: str = Form(""),
                business: Business = Depends(get_current_business), db: Session = Depends(get_db)):
    verify_csrf(request, csrf_token)
    conv = get_owned_conversation(db, business, conversation_id)
    text = message.strip()
    if not text:
        flash(request, "Message cannot be empty.", "error")
        return RedirectResponse(f"/conversations/{conv.id}", status_code=303)

    conv.status = "human"  # replying manually means a human is now handling it
    add_message(db, conv, "human_agent", text[:4000])
    db.commit()

    if conv.channel == "whatsapp":
        config = business.whatsapp_config
        if config and whatsapp.send_text(config, conv.customer.phone, text):
            flash(request, "Reply sent on WhatsApp.")
        else:
            flash(request, "Saved, but sending on WhatsApp failed. Check your WhatsApp settings and logs.", "error")
    else:
        flash(request, "Reply saved. It appears in the WhatsApp Simulator (demo).")
    return RedirectResponse(f"/conversations/{conv.id}", status_code=303)


@router.post("/{conversation_id}/status")
def set_status(request: Request, conversation_id: int, status: str = Form(...), csrf_token: str = Form(""),
               business: Business = Depends(get_current_business), db: Session = Depends(get_db)):
    verify_csrf(request, csrf_token)
    conv = get_owned_conversation(db, business, conversation_id)
    if status not in ("ai", "human"):
        raise HTTPException(status_code=400, detail="Invalid status")
    conv.status = status
    if status == "ai":
        conv.handoff_reason = None
    db.commit()
    flash(request, "The AI agent is handling this chat again." if status == "ai" else "You are now handling this chat.")
    return RedirectResponse(f"/conversations/{conv.id}", status_code=303)
