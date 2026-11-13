"""The owner assistant: the AI agent that manages the business dashboard by chat."""
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import utcnow
from app.models import Business, OwnerMessage
from app.services import agent as agent_service
from app.services.owner_tools import OWNER_TOOL_SCHEMAS, OwnerContext, execute_owner_tool

logger = logging.getLogger(__name__)

NOT_CONFIGURED = "The AI is not configured yet. Add OPENAI_API_KEY to your .env file and restart the server."


@dataclass
class OwnerReply:
    reply: str
    actions: list[str]


def build_owner_prompt(business: Business) -> str:
    return f"""You are the AI business assistant inside the dashboard of "{business.name}".
You help the owner manage orders, products, stock and reports. Today is {utcnow():%A %d %B %Y}.
Prices are in {business.currency}.

Rules:
- Use tools for every number and fact. Never guess.
- When the owner asks for a change, do it with the right tool, then say exactly what changed.
- If a request is ambiguous (for example several products match), ask a short question first.
- To change a product, find it first with find_products to get its product_id.
- Changing an order status notifies the customer automatically unless the owner says not to.
- Keep answers short and clear. Use short bullet lists for several items.
"""


def _history(db: Session, business: Business, limit: int) -> list[dict]:
    rows = db.scalars(
        select(OwnerMessage).where(OwnerMessage.business_id == business.id)
        .order_by(OwnerMessage.id.desc()).limit(limit)
    ).all()
    return [{"role": "user" if m.role == "owner" else "assistant", "content": m.content} for m in reversed(rows)]


def handle_owner_message(db: Session, business: Business, text: str) -> OwnerReply:
    db.add(OwnerMessage(business_id=business.id, role="owner", content=text.strip()[:4000]))
    db.commit()

    ctx = OwnerContext(db=db, business=business)
    try:
        client = agent_service.llm_client_factory()
        messages = [{"role": "system", "content": build_owner_prompt(business)},
                    *_history(db, business, get_settings().agent_max_history)]
        reply, hit_limit = agent_service.run_tool_loop(
            client, messages, OWNER_TOOL_SCHEMAS, lambda name, args: execute_owner_tool(ctx, name, args)
        )
        if hit_limit:
            reply = "That took too many steps. Please break the request into smaller parts."
        reply = reply or "Done."
    except agent_service.AgentNotConfigured:
        reply = NOT_CONFIGURED
    except Exception:
        logger.exception("Owner assistant failed for business %s", business.id)
        db.rollback()
        reply = "Sorry, something went wrong. Please try again."

    db.add(OwnerMessage(business_id=business.id, role="assistant", content=reply, actions=ctx.actions or None))
    db.commit()
    return OwnerReply(reply=reply, actions=ctx.actions)
