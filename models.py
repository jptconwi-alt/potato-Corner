from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    full_name     = db.Column(db.String(100), nullable=False)
    phone         = db.Column(db.String(20))
    # Full delivery address fields
    street        = db.Column(db.String(200))
    barangay      = db.Column(db.String(100))
    city          = db.Column(db.String(100))
    province      = db.Column(db.String(100))
    zipcode       = db.Column(db.String(10))
    address       = db.Column(db.Text)          # legacy / combined
    google_id     = db.Column(db.String(100), unique=True, nullable=True)
    profile_complete = db.Column(db.Boolean, default=False)  # False for new Google users
    is_admin      = db.Column(db.Boolean, default=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    orders        = db.relationship('Order', backref='user', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_full_address(self):
        parts = [self.street, self.barangay, self.city, self.province, self.zipcode]
        return ', '.join(p for p in parts if p) or self.address or ''

    def to_dict(self):
        return {
            'id': self.id, 'username': self.username, 'email': self.email,
            'full_name': self.full_name, 'phone': self.phone,
            'street': self.street, 'barangay': self.barangay,
            'city': self.city, 'province': self.province, 'zipcode': self.zipcode,
            'address': self.get_full_address(),
            'is_admin': self.is_admin, 'profile_complete': self.profile_complete,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M')
        }


class Product(db.Model):
    __tablename__ = 'products'
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(100), nullable=False)
    description  = db.Column(db.Text, nullable=False)
    price        = db.Column(db.Float, nullable=False)
    size         = db.Column(db.String(20), nullable=False)
    flavor       = db.Column(db.String(50), nullable=False)
    image_url    = db.Column(db.String(200), default='fries/cheese.svg')
    is_available = db.Column(db.Boolean, default=True)
    category     = db.Column(db.String(50), default='Fries')
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name, 'description': self.description,
            'price': self.price, 'size': self.size, 'flavor': self.flavor,
            'image_url': self.image_url, 'is_available': self.is_available,
            'category': self.category or 'Fries'
        }


class Order(db.Model):
    __tablename__    = 'orders'
    id               = db.Column(db.Integer, primary_key=True)
    order_number     = db.Column(db.String(20), unique=True, nullable=False)
    user_id          = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    customer_name    = db.Column(db.String(100), nullable=False)
    customer_email   = db.Column(db.String(100), nullable=False)
    customer_phone   = db.Column(db.String(20), nullable=False)
    delivery_address = db.Column(db.Text, nullable=False)
    total_amount     = db.Column(db.Float, nullable=False)
    status           = db.Column(db.String(20), default='Pending')
    payment_method   = db.Column(db.String(50), nullable=False)
    payment_status   = db.Column(db.String(20), default='Unpaid')
    order_date       = db.Column(db.DateTime, default=datetime.utcnow)
    items            = db.relationship('OrderItem', backref='order', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id, 'order_number': self.order_number,
            'customer_name': self.customer_name, 'total_amount': self.total_amount,
            'status': self.status, 'order_date': self.order_date.strftime('%Y-%m-%d %H:%M'),
            'items': [i.to_dict() for i in self.items]
        }


class OrderItem(db.Model):
    __tablename__ = 'order_items'
    id           = db.Column(db.Integer, primary_key=True)
    order_id     = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    product_id   = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    product_name = db.Column(db.String(100), nullable=False)
    quantity     = db.Column(db.Integer, nullable=False)
    price        = db.Column(db.Float, nullable=False)
    subtotal     = db.Column(db.Float, nullable=False)

    def to_dict(self):
        return {
            'product_name': self.product_name, 'quantity': self.quantity,
            'price': self.price, 'subtotal': self.subtotal
        }


class CartItem(db.Model):
    __tablename__ = 'cart_items'
    id         = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), nullable=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity   = db.Column(db.Integer, nullable=False, default=1)
    added_at   = db.Column(db.DateTime, default=datetime.utcnow)
    product    = db.relationship('Product', backref='cart_items')
