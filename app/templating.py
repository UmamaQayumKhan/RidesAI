"""Jinja2 setup, template helpers and flash messages."""
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.money import format_money
from app.security import get_csrf_token

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def flash(request: Request, message: str, category: str = "success") -> None:
    request.session.setdefault("_flashes", [])
    flashes = request.session["_flashes"]
    flashes.append({"message": message, "category": category})
    request.session["_flashes"] = flashes


def pop_flashes(request: Request) -> list[dict]:
    return request.session.pop("_flashes", [])


def _datetime(value, fmt: str = "%d %b %Y, %H:%M") -> str:
    return value.strftime(fmt) if value else ""


templates.env.globals.update(
    csrf_token=get_csrf_token,
    pop_flashes=pop_flashes,
    settings=get_settings(),
)
templates.env.filters["money"] = format_money
templates.env.filters["dt"] = _datetime


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    return templates.TemplateResponse(request, name, context or {}, status_code=status_code)
