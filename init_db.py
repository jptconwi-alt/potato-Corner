from models import db, Product, User

FLAVOR_IMAGES = {
    "Cheese":        "fries/cheese.svg",
    "Sour Cream":    "fries/sour-cream.svg",
    "BBQ":           "fries/bbq.svg",
    "Chili BBQ":     "fries/chili-bbq.svg",
    "Wasabi":        "fries/wasabi.svg",
    "White Cheddar": "fries/white-cheddar.svg",
    "Chili Powder":  "fries/chili-powder.svg",
    "Salted Caramel":"fries/salted-caramel.svg",
}

def run_migrations(db_engine):
    """Add new columns to existing tables without breaking existing data."""
    import sqlalchemy as sa
    with db_engine.connect() as conn:
        inspector = sa.inspect(db_engine)
        cols = [c['name'] for c in inspector.get_columns('products')]
        if 'image_data' not in cols:
            conn.execute(sa.text('ALTER TABLE products ADD COLUMN image_data TEXT'))
            conn.commit()
            print("✅ Migration: added products.image_data column")
        if 'category' not in cols:
            conn.execute(sa.text("ALTER TABLE products ADD COLUMN category TEXT DEFAULT 'Fries'"))
            conn.commit()
            print("✅ Migration: added products.category column")


def init_database():
    # Always ensure a default admin account exists
    if not User.query.filter_by(is_admin=True).first():
        admin = User(
            username='admin',
            email='admin@potatocorner.com',
            full_name='Admin',
            is_admin=True,
            profile_complete=True
        )
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print("✅ Default admin created — username: admin, password: admin123")

    if Product.query.first() is not None:
        return

    flavors = list(FLAVOR_IMAGES.keys())
    sizes   = ["Small", "Medium", "Large", "Mega", "Jumbo"]
    prices  = {"Small": 59, "Medium": 89, "Large": 119, "Mega": 179, "Jumbo": 239}
    descs   = {
        "Cheese":        "Classic golden fries smothered in rich, creamy cheese powder.",
        "Sour Cream":    "Crispy fries coated in tangy sour cream seasoning.",
        "BBQ":           "Smoky BBQ-flavored fries for the BBQ lover in you.",
        "Chili BBQ":     "The best of both worlds — spicy chili and smoky BBQ.",
        "Wasabi":        "A bold wasabi kick for the adventurous snacker.",
        "White Cheddar": "Smooth white cheddar seasoning on perfectly fried potatoes.",
        "Chili Powder":  "Fiery chili powder fries for serious heat seekers.",
        "Salted Caramel":"Sweet and salty caramel-dusted fries — a unique treat.",
    }

    products = []
    for flavor in flavors:
        for size in sizes:
            products.append(Product(
                name=f"{flavor} Fries ({size})",
                description=descs[flavor],
                price=prices[size],
                size=size,
                flavor=flavor,
                image_url=FLAVOR_IMAGES[flavor],
                is_available=True
            ))

    db.session.bulk_save_objects(products)
    db.session.commit()
    print(f"✅ Added {len(products)} products to database")
