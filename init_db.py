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
    """Create the default admin account if none exists yet, and run data migrations."""
    from models import db, User, Product

    # ── Migrate SVG → PNG image URLs ────────────────────────────────────────
    try:
        products = Product.query.all()
        updated = 0
        for p in products:
            if p.image_url and p.image_url.endswith('.svg'):
                p.image_url = p.image_url[:-4] + '.png'
                updated += 1
        if updated:
            db.session.commit()
            print(f'✅ Migrated {updated} product image URLs from .svg → .png')
    except Exception as e:
        print(f'⚠️  Image URL migration skipped: {e}')

    # Credentials – override via environment variables in Vercel dashboard
    admin_username = os.environ.get("ADMIN_USERNAME", "admin")
    admin_email    = os.environ.get("ADMIN_EMAIL",    "admin@potatocorner.com")
    admin_password = os.environ.get("ADMIN_PASSWORD", "Admin@1234")
    admin_name     = os.environ.get("ADMIN_NAME",     "Admin")

    try:
        # Only create if no admin exists at all
        if User.query.filter_by(is_admin=True).first():
            return   # an admin already exists – nothing to do

        # Also skip if username/email is already taken (non-admin account)
        if (User.query.filter_by(username=admin_username).first() or
                User.query.filter_by(email=admin_email).first()):
            # Promote that user to admin instead
            user = (User.query.filter_by(username=admin_username).first() or
                    User.query.filter_by(email=admin_email).first())
            user.is_admin = True
            db.session.commit()
            print(f"✅ Promoted existing user '{user.username}' to admin.")
            return

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
        print(f"✅ Default admin account created → username: '{admin_username}'  password: '{admin_password}'")
    except Exception as e:
        print(f"⚠️  Could not seed admin account: {e}")
