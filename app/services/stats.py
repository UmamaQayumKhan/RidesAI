"""Numbers for the dashboard. Every query is filtered by business_id."""
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import utcnow
from app.models import ORDER_STATUSES, Conversation, Customer, Order, Product

LOW_STOCK_THRESHOLD = 5


def dashboard_stats(db: Session, business_id: int) -> dict:
    def count(model, *where):
        return db.scalar(select(func.count()).select_from(model).where(model.business_id == business_id, *where)) or 0

    revenue = db.scalar(
        select(func.coalesce(func.sum(Order.total_cents), 0)).where(
            Order.business_id == business_id, Order.status != "cancelled"
        )
    ) or 0
    paid_orders = count(Order, Order.status != "cancelled")

    by_status_rows = db.execute(
        select(Order.status, func.count()).where(Order.business_id == business_id).group_by(Order.status)
    ).all()
    by_status = {s: 0 for s in ORDER_STATUSES}
    by_status.update({status: n for status, n in by_status_rows})

    # Revenue for each of the last 7 days (computed in Python so it works on any database).
    today = utcnow().date()
    start = today - timedelta(days=6)
    recent = db.execute(
        select(Order.created_at, Order.total_cents).where(
            Order.business_id == business_id,
            Order.status != "cancelled",
            Order.created_at >= start,
        )
    ).all()
    daily = {start + timedelta(days=i): 0 for i in range(7)}
    for created_at, total in recent:
        if created_at.date() in daily:
            daily[created_at.date()] += total
    peak = max(daily.values()) or 1
    revenue_7d = [
        {"label": d.strftime("%a"), "cents": c, "pct": round(c * 100 / peak)} for d, c in daily.items()
    ]

    return {
        "customers": count(Customer),
        "conversations": count(Conversation),
        "needs_human": count(Conversation, Conversation.status == "human"),
        "orders": count(Order),
        "pending_orders": by_status["pending"],
        "revenue_cents": revenue,
        "avg_order_cents": (revenue // paid_orders) if paid_orders else 0,
        "products": count(Product, Product.is_active.is_(True)),
        "by_status": by_status,
        "revenue_7d": revenue_7d,
        "recent_orders": db.scalars(
            select(Order).where(Order.business_id == business_id).order_by(Order.id.desc()).limit(6)
        ).all(),
        "low_stock": db.scalars(
            select(Product)
            .where(
                Product.business_id == business_id,
                Product.is_active.is_(True),
                Product.stock <= LOW_STOCK_THRESHOLD,
            )
            .order_by(Product.stock)
            .limit(6)
        ).all(),
    }
