"""Agent settings and WhatsApp credentials."""
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_business
from app.models import AgentSettings, Business, WhatsAppConfig
from app.routers import validation_message
from app.schemas import AgentSettingsIn, WhatsAppSettingsIn
from app.security import decrypt_secret, encrypt_secret, mask_secret, new_token, verify_csrf
from app.services import whatsapp
from app.templating import flash, render

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


def _ensure_configs(db: Session, business: Business) -> None:
    if business.agent_settings is None:
        business.agent_settings = AgentSettings()
    if business.whatsapp_config is None:
        business.whatsapp_config = WhatsAppConfig(webhook_key=new_token(18), verify_token=new_token(12))
    db.flush()


@router.get("")
def settings_page(request: Request, business: Business = Depends(get_current_business), db: Session = Depends(get_db)):
    _ensure_configs(db, business)
    db.commit()
    wa = business.whatsapp_config
    return render(request, "settings.html", {
        "business": business,
        "active": "settings",
        "agent": business.agent_settings,
        "wa": wa,
        "token_mask": mask_secret(decrypt_secret(wa.access_token_enc)),
        "secret_mask": mask_secret(decrypt_secret(wa.app_secret_enc)),
        "webhook_url": f"{get_settings().public_base_url.rstrip('/')}/webhooks/whatsapp/{wa.webhook_key}",
        "llm_configured": get_settings().llm_configured,
        "llm_model": get_settings().openai_model,
    })


@router.post("/agent")
def save_agent(
    request: Request,
    business_name: str = Form(...), currency: str = Form("PKR"), agent_name: str = Form(...),
    instructions: str = Form(""), business_info: str = Form(""), handoff_message: str = Form(""),
    csrf_token: str = Form(""),
    business: Business = Depends(get_current_business), db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    try:
        data = AgentSettingsIn(business_name=business_name, currency=currency, agent_name=agent_name,
                               instructions=instructions, business_info=business_info,
                               handoff_message=handoff_message)
    except ValidationError as exc:
        flash(request, validation_message(exc), "error")
        return RedirectResponse("/settings", status_code=303)
    _ensure_configs(db, business)
    business.name, business.currency = data.business_name, data.currency
    agent = business.agent_settings
    agent.agent_name, agent.instructions, agent.business_info = data.agent_name, data.instructions, data.business_info
    if data.handoff_message.strip():
        agent.handoff_message = data.handoff_message.strip()
    db.commit()
    flash(request, "Agent settings saved.")
    return RedirectResponse("/settings", status_code=303)


@router.post("/whatsapp")
def save_whatsapp(
    request: Request,
    phone_number_id: str = Form(""), access_token: str = Form(""), app_secret: str = Form(""),
    verify_token: str = Form(""), is_active: bool = Form(False), csrf_token: str = Form(""),
    business: Business = Depends(get_current_business), db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    try:
        data = WhatsAppSettingsIn(phone_number_id=phone_number_id, access_token=access_token.strip(),
                                  app_secret=app_secret.strip(), verify_token=verify_token.strip(),
                                  is_active=is_active)
    except ValidationError as exc:
        flash(request, validation_message(exc), "error")
        return RedirectResponse("/settings#whatsapp", status_code=303)

    _ensure_configs(db, business)
    wa = business.whatsapp_config
    wa.phone_number_id = data.phone_number_id
    # Empty field = keep the existing secret (secrets are never shown again after saving).
    if data.access_token:
        wa.access_token_enc = encrypt_secret(data.access_token)
    if data.app_secret:
        wa.app_secret_enc = encrypt_secret(data.app_secret)
    if data.verify_token:
        wa.verify_token = data.verify_token

    if data.is_active and not (wa.phone_number_id and wa.access_token_enc and wa.app_secret_enc and wa.verify_token):
        wa.is_active = False
        db.commit()
        flash(request, "Saved, but live mode stays OFF until Phone Number ID, Access Token, App Secret "
                       "and Verify Token are all filled in.", "error")
        return RedirectResponse("/settings#whatsapp", status_code=303)

    wa.is_active = data.is_active
    db.commit()
    logger.info("WhatsApp settings updated for business %s (active=%s)", business.id, wa.is_active)
    flash(request, "WhatsApp settings saved." + (" Live mode is ON." if wa.is_active else ""))
    return RedirectResponse("/settings#whatsapp", status_code=303)


@router.post("/whatsapp/test")
def send_test_message(request: Request, to_phone: str = Form(...), csrf_token: str = Form(""),
                      business: Business = Depends(get_current_business)):
    verify_csrf(request, csrf_token)
    phone = "".join(ch for ch in to_phone if ch.isdigit())
    config = business.whatsapp_config
    if not phone or config is None:
        flash(request, "Enter a phone number with country code, e.g. 923001234567.", "error")
    elif whatsapp.send_text(config, phone, f"Hello from {business.name}! Your WhatsApp integration works."):
        flash(request, f"Test message sent to {phone}.")
    else:
        flash(request, "Test message failed. Check that live mode is ON, the token is valid, and the number "
                       "is added as a test recipient in Meta. See the server log for Meta's error.", "error")
    return RedirectResponse("/settings#whatsapp", status_code=303)
