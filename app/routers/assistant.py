"""Owner AI assistant: manage the dashboard by chatting with the agent."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_business
from app.models import Business, OwnerMessage
from app.security import require_csrf_header
from app.services.owner_assistant import handle_owner_message
from app.templating import render

router = APIRouter(tags=["owner-assistant"])


class OwnerMessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Message cannot be empty")
        return v


def _history(db: Session, business: Business) -> list[dict]:
    rows = db.scalars(
        select(OwnerMessage).where(OwnerMessage.business_id == business.id).order_by(OwnerMessage.id)
    ).all()
    return [{"id": m.id, "role": m.role, "content": m.content, "actions": m.actions or [],
             "created_at": m.created_at.isoformat()} for m in rows[-100:]]


@router.get("/assistant")
def assistant_page(request: Request, business: Business = Depends(get_current_business)):
    return render(request, "assistant.html", {"business": business, "active": "assistant",
                                              "llm_configured": get_settings().llm_configured})


@router.get("/api/assistant/messages")
def assistant_history(business: Business = Depends(get_current_business), db: Session = Depends(get_db)):
    return {"messages": _history(db, business)}


@router.post("/api/assistant/message", dependencies=[Depends(require_csrf_header)])
def assistant_message(data: OwnerMessageIn, business: Business = Depends(get_current_business),
                      db: Session = Depends(get_db)):
    result = handle_owner_message(db, business, data.message)
    return {"reply": result.reply, "actions": result.actions, "messages": _history(db, business)}


@router.post("/api/assistant/clear", dependencies=[Depends(require_csrf_header)])
def assistant_clear(business: Business = Depends(get_current_business), db: Session = Depends(get_db)):
    db.execute(delete(OwnerMessage).where(OwnerMessage.business_id == business.id))
    db.commit()
    return {"status": "cleared"}
