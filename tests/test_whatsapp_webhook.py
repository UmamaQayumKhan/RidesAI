"""WhatsApp Cloud API webhook: verification, signatures, duplicates, sending."""
import hashlib
import hmac
import json

import pytest
from sqlalchemy import func, select

from app.models import Message, WhatsAppConfig
from app.security import encrypt_secret
from app.services import whatsapp
from app.services.accounts import create_business

APP_SECRET = "test-app-secret"
PHONE_ID = "1112223334"


@pytest.fixture
def live_business(db):
    business = create_business(db, name="Live Shop", email="live@shop-example.com", password="password123")
    cfg = business.whatsapp_config
    cfg.phone_number_id = PHONE_ID
    cfg.access_token_enc = encrypt_secret("EAAG-test-token")
    cfg.app_secret_enc = encrypt_secret(APP_SECRET)
    cfg.verify_token = "my-verify-token"
    cfg.is_active = True
    db.commit()
    return business, cfg.webhook_key


@pytest.fixture
def sent(monkeypatch):
    outbox = []
    monkeypatch.setattr(whatsapp, "send_text", lambda cfg, to, body: outbox.append((to, body)) or True)
    return outbox


def payload(text="Hi, do you have earbuds?", msg_id="wamid.ABC123", phone_id=PHONE_ID):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "15550001111", "phone_number_id": phone_id},
            "contacts": [{"profile": {"name": "Wisha Qayum Khan"}, "wa_id": "923331234567"}],
            "messages": [{"from": "923331234567", "id": msg_id, "timestamp": "1700000000",
                          "type": "text", "text": {"body": text}}],
        }}]}],
    }


def signed_post(client, key, body: dict, secret=APP_SECRET):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(f"/webhooks/whatsapp/{key}", content=raw,
                       headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})


def test_verify_handshake(client, live_business):
    _, key = live_business
    ok = client.get(f"/webhooks/whatsapp/{key}",
                    params={"hub.mode": "subscribe", "hub.verify_token": "my-verify-token", "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    bad = client.get(f"/webhooks/whatsapp/{key}",
                     params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"})
    assert bad.status_code == 403
    assert client.get("/webhooks/whatsapp/unknown-key").status_code == 404


def test_rejects_bad_signature(client, live_business, sent):
    _, key = live_business
    assert signed_post(client, key, payload(), secret="wrong-secret").status_code == 401
    unsigned = client.post(f"/webhooks/whatsapp/{key}", json=payload())
    assert unsigned.status_code == 401
    assert sent == []


def test_incoming_message_gets_ai_reply_and_duplicates_are_ignored(client, live_business, sent, fake_llm, db):
    _, key = live_business
    llm = fake_llm([("text", "Yes! We have Wireless Earbuds."), ("text", "should never be used")])

    assert signed_post(client, key, payload()).status_code == 200
    assert sent == [("923331234567", "Yes! We have Wireless Earbuds.")]

    # Meta retries the same message id: must not be answered twice.
    assert signed_post(client, key, payload()).status_code == 200
    assert len(sent) == 1
    assert len(llm.calls) == 1
    count = db.scalar(select(func.count()).select_from(Message).where(Message.external_id == "wamid.ABC123"))
    assert count == 1


def test_non_text_message_gets_fixed_reply(client, live_business, sent, fake_llm):
    _, key = live_business
    llm = fake_llm([])
    body = payload()
    msg = body["entry"][0]["changes"][0]["value"]["messages"][0]
    msg["type"], msg["id"] = "image", "wamid.IMG1"
    del msg["text"]
    msg["image"] = {"id": "media-id"}
    assert signed_post(client, key, body).status_code == 200
    assert len(sent) == 1 and "only read text" in sent[0][1]
    assert llm.calls == []


def test_ignores_other_phone_number_ids_and_inactive(client, live_business, sent, fake_llm, db):
    business, key = live_business
    fake_llm([])
    assert signed_post(client, key, payload(phone_id="999", msg_id="wamid.X")).status_code == 200
    assert sent == []
    cfg = db.scalar(select(WhatsAppConfig).where(WhatsAppConfig.business_id == business.id))
    cfg.is_active = False
    db.commit()
    assert signed_post(client, key, payload(msg_id="wamid.Y")).json()["status"] == "ignored"


def test_signature_and_parser_units():
    body = b'{"a":1}'
    good = "sha256=" + hmac.new(b"s", body, hashlib.sha256).hexdigest()
    assert whatsapp.verify_signature("s", body, good)
    assert not whatsapp.verify_signature("s", body, "sha256=deadbeef")
    assert not whatsapp.verify_signature("", body, good)
    msgs = whatsapp.parse_incoming(payload("hello"))
    assert msgs[0].text == "hello" and msgs[0].profile_name == "Wisha Qayum Khan" and msgs[0].phone_number_id == PHONE_ID
    assert whatsapp.parse_incoming({"object": "page"}) == []
