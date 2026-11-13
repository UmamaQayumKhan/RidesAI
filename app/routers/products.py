"""Product management pages (HTML forms)."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_business
from app.models import Business, Product
from app.money import from_cents, to_cents
from app.routers import validation_message
from app.schemas import ProductIn
from app.security import verify_csrf
from app.templating import flash, render

router = APIRouter(prefix="/products", tags=["products"])


def get_owned_product(db: Session, business: Business, product_id: int) -> Product:
    """Load a product ONLY if it belongs to the logged-in business (else 404)."""
    product = db.scalar(select(Product).where(Product.id == product_id, Product.business_id == business.id))
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.get("")
def list_products(request: Request, q: str = "", business: Business = Depends(get_current_business),
                  db: Session = Depends(get_db)):
    stmt = select(Product).where(Product.business_id == business.id)
    if q.strip():
        stmt = stmt.where(Product.name.ilike(f"%{q.strip()}%"))
    products = db.scalars(stmt.order_by(Product.name)).all()
    return render(request, "products.html", {"business": business, "active": "products", "products": products,
                                             "q": q, "from_cents": from_cents})


@router.post("")
def create_product(
    request: Request,
    name: str = Form(...), description: str = Form(""), sku: str = Form(""),
    price: str = Form(...), stock: int = Form(0), is_active: bool = Form(False),
    csrf_token: str = Form(""),
    business: Business = Depends(get_current_business), db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    try:
        data = ProductIn(name=name, description=description, sku=sku, price=price, stock=stock, is_active=is_active)
    except ValidationError as exc:
        flash(request, validation_message(exc), "error")
        return RedirectResponse("/products", status_code=303)
    db.add(Product(business_id=business.id, name=data.name, description=data.description, sku=data.sku,
                   price_cents=to_cents(data.price), stock=data.stock, is_active=data.is_active))
    db.commit()
    flash(request, f"Product '{data.name}' added.")
    return RedirectResponse("/products", status_code=303)


@router.post("/{product_id}/edit")
def edit_product(
    request: Request, product_id: int,
    name: str = Form(...), description: str = Form(""), sku: str = Form(""),
    price: str = Form(...), stock: int = Form(0), is_active: bool = Form(False),
    csrf_token: str = Form(""),
    business: Business = Depends(get_current_business), db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    product = get_owned_product(db, business, product_id)
    try:
        data = ProductIn(name=name, description=description, sku=sku, price=price, stock=stock, is_active=is_active)
    except ValidationError as exc:
        flash(request, validation_message(exc), "error")
        return RedirectResponse("/products", status_code=303)
    product.name, product.description, product.sku = data.name, data.description, data.sku
    product.price_cents, product.stock, product.is_active = to_cents(data.price), data.stock, data.is_active
    db.commit()
    flash(request, f"Product '{product.name}' updated.")
    return RedirectResponse("/products", status_code=303)


@router.post("/{product_id}/delete")
def delete_product(request: Request, product_id: int, csrf_token: str = Form(""),
                   business: Business = Depends(get_current_business), db: Session = Depends(get_db)):
    verify_csrf(request, csrf_token)
    product = get_owned_product(db, business, product_id)
    name = product.name
    db.delete(product)  # past orders keep a copy of name and price (OrderItem)
    db.commit()
    flash(request, f"Product '{name}' deleted.")
    return RedirectResponse("/products", status_code=303)
