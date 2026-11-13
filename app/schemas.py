"""Pydantic schemas: validate input and shape API output."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import ORDER_STATUSES


# ------------------------------------------------------------------- auth
class RegisterIn(BaseModel):
    business_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("business_name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Business name is too short")
        return v


# --------------------------------------------------------------- products
class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    sku: str | None = Field(default=None, max_length=64)
    price: str = Field(description="Price in normal units, e.g. '1499.99'")
    stock: int = Field(ge=0, le=1_000_000)
    is_active: bool = True

    @field_validator("name", "description")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("sku")
    @classmethod
    def blank_sku_is_none(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None

    @field_validator("price")
    @classmethod
    def valid_price(cls, v: str) -> str:
        from app.money import to_cents

        to_cents(v)  # raises ValueError with a friendly message
        return v


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    sku: str | None
    price_cents: int
    stock: int
    is_active: bool


# ----------------------------------------------------------------- orders
class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int | None
    product_name: str
    unit_price_cents: int
    quantity: int
    line_total_cents: int


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str
    status: str
    customer_name: str
    phone: str
    delivery_address: str
    total_cents: int
    source: str
    created_at: datetime
    items: list[OrderItemOut]


class OrderStatusIn(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def valid_status(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ORDER_STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(ORDER_STATUSES)}")
        return v


# --------------------------------------------------------------- settings
class AgentSettingsIn(BaseModel):
    business_name: str = Field(min_length=2, max_length=120)
    currency: str = Field(min_length=1, max_length=8)
    agent_name: str = Field(min_length=1, max_length=60)
    instructions: str = Field(default="", max_length=4000)
    business_info: str = Field(default="", max_length=8000)
    handoff_message: str = Field(default="", max_length=500)

    @field_validator("currency")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.strip().upper()


class WhatsAppSettingsIn(BaseModel):
    phone_number_id: str = Field(default="", max_length=64)
    access_token: str = Field(default="", max_length=1024)
    app_secret: str = Field(default="", max_length=256)
    verify_token: str = Field(default="", max_length=128)
    is_active: bool = False

    @field_validator("phone_number_id")
    @classmethod
    def digits_only(cls, v: str) -> str:
        v = v.strip()
        if v and not v.isdigit():
            raise ValueError("Phone number ID should contain digits only (copy it from Meta > WhatsApp > API Setup)")
        return v


# -------------------------------------------------------------- simulator
class SimulatorMessageIn(BaseModel):
    phone: str = Field(min_length=6, max_length=32)
    name: str | None = Field(default=None, max_length=120)
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, v: str) -> str:
        cleaned = "".join(ch for ch in v if ch.isdigit())
        if len(cleaned) < 6:
            raise ValueError("Enter a valid phone number")
        return cleaned

    @field_validator("message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Message cannot be empty")
        return v


class SimulatorResetIn(BaseModel):
    phone: str = Field(min_length=6, max_length=32)

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, v: str) -> str:
        return "".join(ch for ch in v if ch.isdigit())


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime


class SimulatorReplyOut(BaseModel):
    conversation_id: int
    conversation_status: str
    reply: str | None
    messages: list[ChatMessageOut]
