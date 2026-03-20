from models import db, Product

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

def init_database():
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
