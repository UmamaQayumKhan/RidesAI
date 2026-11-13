"""Register, log in, log out."""
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers import validation_message
from app.schemas import RegisterIn
from app.security import verify_csrf, verify_password
from app.services.accounts import add_sample_activity, add_sample_products, create_business, get_business_by_email
from app.templating import flash, render

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


def _login_session(request: Request, business_id: int) -> None:
    csrf = request.session.get("csrf_token")
    request.session.clear()  # new session on login (prevents session fixation)
    request.session["business_id"] = business_id
    if csrf:
        request.session["csrf_token"] = csrf


@router.get("/login")
def login_page(request: Request):
    if request.session.get("business_id"):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "login.html")


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    business = get_business_by_email(db, email)
    if business is None or not verify_password(password, business.password_hash):
        logger.info("Failed login for %s", email)
        return render(request, "login.html", {"error": "Wrong email or password.", "email": email}, 400)
    _login_session(request, business.id)
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/register")
def register_page(request: Request):
    return render(request, "register.html")


@router.post("/register")
def register(
    request: Request,
    business_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    sample_data: bool = Form(False),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    form = {"business_name": business_name, "email": email}
    try:
        data = RegisterIn(business_name=business_name, email=email, password=password)
    except ValidationError as exc:
        return render(request, "register.html", {"error": validation_message(exc), **form}, 400)

    if get_business_by_email(db, data.email):
        return render(request, "register.html", {"error": "An account with this email already exists.", **form}, 400)

    business = create_business(db, name=data.business_name, email=data.email, password=data.password)
    if sample_data:
        add_sample_products(db, business)
        add_sample_activity(db, business)
    db.commit()
    logger.info("New business registered: id=%s", business.id)
    _login_session(request, business.id)
    flash(request, f"Welcome to AgentDesk, {business.name}!")
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    verify_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
