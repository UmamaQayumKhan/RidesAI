"""Order pages (HTML)."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_business
from app.models import ORDER_STATUSES, Business, Order
from app.security import verify_csrf
from app.services.notifications import change_status_and_notify
from app.services.orders import OrderError
from app.templating import flash, render

router = APIRouter(prefix="/orders", tags=["orders"])


def get_owned_order(db: Session, business: Business, order_id: int) -> Order:
    order = db.scalar(select(Order).where(Order.id == order_id, Order.business_id == business.id))
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.get("")
def list_orders(request: Request, status: str = "", business: Business = Depends(get_current_business),
                db: Session = Depends(get_db)):
    stmt = select(Order).where(Order.business_id == business.id)
    if status in ORDER_STATUSES:
        stmt = stmt.where(Order.status == status)
    orders = db.scalars(stmt.order_by(Order.id.desc()).limit(500)).all()
    return render(request, "orders.html", {"business": business, "active": "orders", "orders": orders,
                                           "statuses": ORDER_STATUSES, "current_status": status})


@router.get("/{order_id}")
def order_detail(request: Request, order_id: int, business: Business = Depends(get_current_business),
                 db: Session = Depends(get_db)):
    order = get_owned_order(db, business, order_id)
    return render(request, "order_detail.html", {"business": business, "active": "orders", "order": order,
                                                 "statuses": ORDER_STATUSES})


@router.post("/{order_id}/status")
def change_status(request: Request, order_id: int, status: str = Form(...), csrf_token: str = Form(""),
                  next: str = Form(""), notify: str = Form("true"),
                  business: Business = Depends(get_current_business), db: Session = Depends(get_db)):
    verify_csrf(request, csrf_token)
    order = get_owned_order(db, business, order_id)
    try:
        result = change_status_and_notify(db, business, order, status, notify=notify == "true")
        if result.changed:
            flash(request, f"Order {order.number} is now '{order.status}'. {result.notification}")
    except OrderError as exc:
        db.rollback()
        flash(request, str(exc), "error")
    target = next if next.startswith("/orders") else f"/orders/{order.id}"
    return RedirectResponse(target, status_code=303)
