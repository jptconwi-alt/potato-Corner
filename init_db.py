"""
init_db.py – Database initialization, migrations, and seed data.
Runs once on every cold start (safe to run multiple times – all checks
are idempotent).
"""
import os
from sqlalchemy import text


# ─────────────────────────────────────────────────────────────────────────────
# Migrations – add columns that may be missing from older deployments
# ─────────────────────────────────────────────────────────────────────────────
def run_migrations(engine):
    migrations = [
        "ALTER TABLE user ADD COLUMN google_id TEXT",
        "ALTER TABLE user ADD COLUMN profile_complete BOOLEAN DEFAULT 0",
        "ALTER TABLE user ADD COLUMN phone TEXT DEFAULT ''",
        "ALTER TABLE user ADD COLUMN street TEXT DEFAULT ''",
        "ALTER TABLE user ADD COLUMN barangay TEXT DEFAULT ''",
        "ALTER TABLE user ADD COLUMN city TEXT DEFAULT ''",
        "ALTER TABLE user ADD COLUMN province TEXT DEFAULT ''",
        "ALTER TABLE user ADD COLUMN zipcode TEXT DEFAULT ''",
        "ALTER TABLE product ADD COLUMN is_available BOOLEAN DEFAULT 1",
        "ALTER TABLE product ADD COLUMN image_data TEXT",
        "ALTER TABLE orders ADD COLUMN delivery_lat REAL",
        "ALTER TABLE orders ADD COLUMN delivery_lng REAL",
        "ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT 1",
        "ALTER TABLE cart_items ADD COLUMN is_ordered BOOLEAN NOT NULL DEFAULT 0",
    ]
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass   # column already exists – safe to ignore


# ─────────────────────────────────────────────────────────────────────────────
# Seed – default admin account
# ─────────────────────────────────────────────────────────────────────────────
def init_database():
    """Ensure the default admin account exists with the correct password."""
    from models import db, User, Product

    # ── Flavor → image mapping ───────────────────────────────────────────────
    FLAVOR_IMAGE_MAP = {
        'cheese': 'fries/cheese.png',
        'white cheddar': 'fries/white-cheddar.png',
        'bbq': 'fries/bbq.png',
        'chili bbq': 'fries/chili-bbq.png',
        'chili-bbq': 'fries/chili-bbq.png',
        'chili powder': 'fries/chili-powder.png',
        'chili': 'fries/chili-powder.png',
        'salted caramel': 'fries/salted-caramel.png',
        'caramel': 'fries/salted-caramel.png',
        'sour cream': 'fries/sour-cream.png',
        'wasabi': 'fries/wasabi.png',
    }

    # ── Migrate SVG → PNG image URLs & fix flavor-image mismatches ──────────
    try:
        products = Product.query.all()
        updated = 0
        for p in products:
            changed = False
            # Fix .svg → .png
            if p.image_url and p.image_url.endswith('.svg'):
                p.image_url = p.image_url[:-4] + '.png'
                changed = True
            # Auto-assign correct flavor image if still using generic default or wrong flavor image
            if p.image_url and not p.image_data and p.image_url != '_data_':
                flavor_key = (p.flavor or '').lower().strip()
                correct_img = FLAVOR_IMAGE_MAP.get(flavor_key)
                if correct_img and p.image_url != correct_img:
                    p.image_url = correct_img
                    changed = True
            if changed:
                updated += 1
        if updated:
            db.session.commit()
            print(f'✅ Updated {updated} product image URLs (SVG→PNG / flavor mapping)')
    except Exception as e:
        print(f'⚠️  Image URL migration skipped: {e}')

    # Credentials — override via Vercel environment variables
    admin_username = os.environ.get("ADMIN_USERNAME", "admin")
    admin_email    = os.environ.get("ADMIN_EMAIL",    "admin@potatocorner.com")
    admin_password = os.environ.get("ADMIN_PASSWORD", "Admin@1234")
    admin_name     = os.environ.get("ADMIN_NAME",     "Admin")

    try:
        # Find existing admin by username or email
        admin = (User.query.filter_by(username=admin_username).first() or
                 User.query.filter_by(email=admin_email).first() or
                 User.query.filter_by(is_admin=True).first())

        if admin:
            # Always force correct password + admin flag on every startup
            admin.is_admin = True
            admin.profile_complete = True
            admin.set_password(admin_password)
            db.session.commit()
            print(f"✅ Admin account verified → username: '{admin.username}'  password: '{admin_password}'")
        else:
            # No admin at all — create one
            admin = User(
                username=admin_username,
                email=admin_email,
                full_name=admin_name,
                phone="",
                is_admin=True,
                profile_complete=True,
            )
            admin.set_password(admin_password)
            db.session.add(admin)
            db.session.commit()
            print(f"✅ Admin account created → username: '{admin_username}'  password: '{admin_password}'")
    except Exception as e:
        print(f"⚠️  Could not seed admin account: {e}")