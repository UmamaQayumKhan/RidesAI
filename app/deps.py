"""Shared FastAPI dependencies."""
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Business


class NotAuthenticated(Exception):
    """Raised when no business is logged in. Handled in main.py."""


def get_current_business(request: Request, db: Session = Depends(get_db)) -> Business:
    business_id = request.session.get("business_id")
    if not business_id:
        raise NotAuthenticated()
    business = db.get(Business, business_id)
    if business is None:
        request.session.clear()
        raise NotAuthenticated()
    return business
