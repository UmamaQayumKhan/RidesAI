"""Dashboard home and customers page."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_business
from app.models import Business, Customer, Order
from app.services.stats import dashboard_stats
from app.templating import render

router = APIRouter(tags=["dashboard"])


@router.get("/")
def home(request: Request):
    return RedirectResponse("/dashboard" if request.session.get("business_id") else "/login", status_code=303)


@router.get("/dashboard")
def dashboard(request: Request, business: Business = Depends(get_current_business), db: Session = Depends(get_db)):
    return render(request, "dashboard.html", {"business": business, "active": "dashboard",
                                              "stats": dashboard_stats(db, business.id)})


@router.get("/customers")
def customers(request: Request, business: Business = Depends(get_current_business), db: Session = Depends(get_db)):
    order_count = func.count(Order.id)
    spent = func.coalesce(func.sum(Order.total_cents), 0)
    rows = db.execute(
        select(Customer, order_count, spent)
        .outerjoin(Order, (Order.customer_id == Customer.id) & (Order.status != "cancelled"))
        .where(Customer.business_id == business.id)
        .group_by(Customer.id)
        .order_by(Customer.created_at.desc())
    ).all()
    return render(request, "customers.html", {"business": business, "active": "customers", "rows": rows})
