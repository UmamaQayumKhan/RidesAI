"""Create a demo business with sample products, customers, chats and orders.

Usage (from the project folder):
    python -m scripts.seed            # creates the demo account if missing
    python -m scripts.seed --reset    # deletes and recreates the demo account
"""
import argparse

from app.database import SessionLocal, init_db
from app.services.accounts import add_sample_activity, add_sample_products, create_business, get_business_by_email

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo12345"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete and recreate the demo account")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        existing = get_business_by_email(db, DEMO_EMAIL)
        if existing and not args.reset:
            print(f"Demo account already exists. Log in with {DEMO_EMAIL} / {DEMO_PASSWORD}")
            return
        if existing:
            db.delete(existing)
            db.commit()

        business = create_business(db, name="Bright Gadgets", email=DEMO_EMAIL, password=DEMO_PASSWORD)
        add_sample_products(db, business)
        add_sample_activity(db, business)
        db.commit()
        print("Demo data created.")
        print(f"  Login:    {DEMO_EMAIL}")
        print(f"  Password: {DEMO_PASSWORD}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
