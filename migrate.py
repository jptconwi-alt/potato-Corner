from app import app
from models import db

with app.app_context():
    with db.engine.connect() as conn:
        try:
            conn.execute(db.text("ALTER TABLE orders ADD COLUMN delivery_lat FLOAT"))
            print("Added delivery_lat")
        except Exception as e:
            print(f"delivery_lat skipped: {e}")

        try:
            conn.execute(db.text("ALTER TABLE orders ADD COLUMN delivery_lng FLOAT"))
            print("Added delivery_lng")
        except Exception as e:
            print(f"delivery_lng skipped: {e}")

        try:
            conn.execute(db.text("ALTER TABLE order_items ADD COLUMN size VARCHAR(20)"))
            print("Added order_items.size")
        except Exception as e:
            print(f"order_items.size skipped: {e}")

        try:
            conn.execute(db.text("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT 1"))
            print("Added users.is_active")
        except Exception as e:
            print(f"users.is_active skipped: {e}")

        try:
            conn.execute(db.text("ALTER TABLE cart_items ADD COLUMN is_ordered BOOLEAN NOT NULL DEFAULT 0"))
            print("Added cart_items.is_ordered")
        except Exception as e:
            print(f"cart_items.is_ordered skipped: {e}")

        conn.commit()

    print("Migration complete!")