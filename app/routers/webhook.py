"""LIVE MODE: Meta WhatsApp Cloud API webhook.

Each business gets its own URL: /webhooks/whatsapp/{webhook_key}
- GET  = Meta's one-time verification handshake.
- POST = incoming messages. We check the signature, answer Meta with 200 right
         away, and process the message in the background.
"""
import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import Business, WhatsAppConfig
from app.security import decrypt_secret
from app.services import whatsapp
from app.services.conversation import handle_incoming_message

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["whatsapp-webhook"])

UNSUPPORTED_REPLY = "Sorry, I can only read text messages right now. Please type your question."


def _config_by_key(db: Session, webhook_key: str) -> WhatsAppConfig:
    config = db.scalar(select(WhatsAppConfig).where(WhatsAppConfig.webhook_key == webhook_key))
    if config is None:
        raise HTTPException(status_code=404, detail="Unknown webhook")
    return config


@router.get("/whatsapp/{webhook_key}")
def verify_webhook(webhook_key: str, request: Request, db: Session = Depends(get_db)):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token", "")
    challenge = params.get("hub.challenge", "")
    config = _config_by_key(db, webhook_key)
    if mode == "subscribe" and config.verify_token and hmac.compare_digest(token, config.verify_token):
        logger.info("WhatsApp webhook verified for business %s", config.business_id)
        return PlainTextResponse(challenge)
    logger.warning("WhatsApp webhook verification failed for business %s", config.business_id)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp/{webhook_key}")
async def receive_webhook(webhook_key: str, request: Request, background_tasks: BackgroundTasks,
                          db: Session = Depends(get_db)):
    body = await request.body()  # raw bytes are needed for the signature check
    config = _config_by_key(db, webhook_key)

    if not whatsapp.verify_signature(decrypt_secret(config.app_secret_enc), body,
                                     request.headers.get("X-Hub-Signature-256")):
        logger.warning("Invalid WhatsApp signature for business %s", config.business_id)
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    if not config.is_active:
        logger.info("Webhook received for business %s but live mode is OFF; ignoring", config.business_id)
        return {"status": "ignored"}

    for msg in whatsapp.parse_incoming(payload):
        if msg.phone_number_id != config.phone_number_id:
            logger.warning("Message for phone_number_id %s does not match business %s; skipped",
                           msg.phone_number_id, config.business_id)
            continue
        background_tasks.add_task(process_whatsapp_message, config.business_id, msg)

    return {"status": "received"}


def process_whatsapp_message(business_id: int, msg: whatsapp.IncomingMessage) -> None:
    """Runs after Meta already got its 200 OK. Uses its own DB session."""
    db = SessionLocal()
    try:
        business = db.get(Business, business_id)
        if business is None or business.whatsapp_config is None:
            return
        is_text = bool(msg.text.strip())
        # Images, voice notes, stickers: saved (and de-duplicated) but answered with a fixed reply.
        result = handle_incoming_message(
            db, business,
            phone=msg.from_phone,
            text=msg.text if is_text else f"[{msg.type} message]",
            channel="whatsapp",
            external_id=msg.message_id,
            profile_name=msg.profile_name,
            fixed_reply=None if is_text else UNSUPPORTED_REPLY,
        )
        if result.reply and not result.duplicate:
            whatsapp.send_text(business.whatsapp_config, msg.from_phone, result.reply)
    except Exception:
        logger.exception("Failed to process WhatsApp message %s", msg.message_id)
    finally:
        db.close()
