"""Meta WhatsApp Cloud API integration (live mode).

- verify_signature: checks the X-Hub-Signature-256 header Meta adds to each webhook.
- parse_incoming:   pulls customer messages out of Meta's nested webhook JSON.
- send_text:        sends a reply through the Graph API.
"""
import hashlib
import hmac
import logging
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.models import WhatsAppConfig
from app.security import decrypt_secret

logger = logging.getLogger(__name__)

WHATSAPP_MAX_TEXT = 4096


@dataclass
class IncomingMessage:
    phone_number_id: str
    from_phone: str
    message_id: str
    type: str
    text: str
    profile_name: str | None = None


def verify_signature(app_secret: str, body: bytes, signature_header: str | None) -> bool:
    """Meta signs the raw body with HMAC-SHA256 using your App Secret."""
    if not app_secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    received = signature_header.split("=", 1)[1].strip()
    return hmac.compare_digest(expected, received)


def parse_incoming(payload: dict) -> list[IncomingMessage]:
    """Extract messages. Status updates (sent/delivered/read) are ignored."""
    found: list[IncomingMessage] = []
    if payload.get("object") != "whatsapp_business_account":
        return found
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            phone_number_id = str((value.get("metadata") or {}).get("phone_number_id", ""))
            names = {
                c.get("wa_id"): (c.get("profile") or {}).get("name")
                for c in value.get("contacts", []) or []
            }
            for msg in value.get("messages", []) or []:
                msg_type = msg.get("type", "unknown")
                if msg_type == "text":
                    text = (msg.get("text") or {}).get("body", "")
                elif msg_type == "button":
                    text = (msg.get("button") or {}).get("text", "")
                elif msg_type == "interactive":
                    inter = msg.get("interactive") or {}
                    text = (inter.get("button_reply") or inter.get("list_reply") or {}).get("title", "")
                else:
                    text = ""
                found.append(
                    IncomingMessage(
                        phone_number_id=phone_number_id,
                        from_phone=str(msg.get("from", "")),
                        message_id=str(msg.get("id", "")),
                        type=msg_type,
                        text=text,
                        profile_name=names.get(msg.get("from")),
                    )
                )
    return found


def send_text(config: WhatsAppConfig, to_phone: str, body: str) -> bool:
    """Send a text message. Returns True on success. Never raises."""
    settings = get_settings()
    token = decrypt_secret(config.access_token_enc)
    if not (config.is_active and token and config.phone_number_id):
        logger.warning("WhatsApp is not fully configured for business %s; message not sent", config.business_id)
        return False

    url = f"https://graph.facebook.com/{settings.whatsapp_graph_version}/{config.phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "text",
        "text": {"preview_url": False, "body": body[:WHATSAPP_MAX_TEXT]},
    }
    try:
        response = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=settings.whatsapp_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        logger.error("WhatsApp send failed (network): %s", exc)
        return False
    if response.status_code >= 400:
        # Log Meta's error message, never the token.
        logger.error("WhatsApp send failed (%s): %s", response.status_code, response.text[:500])
        return False
    logger.info("WhatsApp message sent to %s", to_phone[-4:].rjust(len(to_phone), "*"))
    return True
