"""JSON REST API (session-authenticated). Interactive docs at /docs."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_business
from app.models import ORDER_STATUSES, Business, Order, Product
from app.money import to_cents
from app.schemas import OrderOut, OrderStatusIn, ProductIn, ProductOut
from app.security import require_csrf_header
from app.services.notifications import change_status_and_notify
from app.services.orders import OrderError
from app.services.stats import dashboard_stats

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/me")
def me(business: Business = Depends(get_current_business)):
    return {"id": business.id, "name": business.name, "email": business.email, "currency": business.currency}


@router.get("/stats")
def stats(business: Business = Depends(get_current_business), db: Session = Depends(get_db)):
    s = dashboard_stats(db, business.id)
    return {k: v for k, v in s.items() if k not in ("recent_orders", "low_stock")}


@router.get("/products", response_model=list[ProductOut])
def list_products(business: Business = Depends(get_current_business), db: Session = Depends(get_db)):
    return db.scalars(select(Product).where(Product.business_id == business.id).order_by(Product.name)).all()


@router.post("/products", response_model=ProductOut, status_code=201, dependencies=[Depends(require_csrf_header)])
def create_product(data: ProductIn, business: Business = Depends(get_current_business), db: Session = Depends(get_db)):
    product = Product(business_id=business.id, name=data.name, description=data.description, sku=data.sku,
                      price_cents=to_cents(data.price), stock=data.stock, is_active=data.is_active)
    db.add(product)
    db.commit()
    return product


@router.put("/products/{product_id}", response_model=ProductOut, dependencies=[Depends(require_csrf_header)])
def update_product(product_id: int, data: ProductIn, business: Business = Depends(get_current_business),
                   db: Session = Depends(get_db)):
    product = db.scalar(select(Product).where(Product.id == product_id, Product.business_id == business.id))
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    product.name, product.description, product.sku = data.name, data.description, data.sku
    product.price_cents, product.stock, product.is_active = to_cents(data.price), data.stock, data.is_active
    db.commit()
    return product


@router.get("/orders", response_model=list[OrderOut])
def list_orders(status: str | None = None, business: Business = Depends(get_current_business),
                db: Session = Depends(get_db)):
    stmt = select(Order).where(Order.business_id == business.id)
    if status:
        if status not in ORDER_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {ORDER_STATUSES}")
        stmt = stmt.where(Order.status == status)
    return db.scalars(stmt.order_by(Order.id.desc()).limit(500)).all()


@router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: int, business: Business = Depends(get_current_business), db: Session = Depends(get_db)):
    order = db.scalar(select(Order).where(Order.id == order_id, Order.business_id == business.id))
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.patch("/orders/{order_id}/status", response_model=OrderOut, dependencies=[Depends(require_csrf_header)])
def patch_order_status(order_id: int, data: OrderStatusIn, notify: bool = True, business: Business = Depends(get_current_business),
                       db: Session = Depends(get_db)):
    order = db.scalar(select(Order).where(Order.id == order_id, Order.business_id == business.id))
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    try:
        change_status_and_notify(db, business, order, data.status, notify=notify)
    except OrderError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return order
