import random
import string
from datetime import datetime
from models import db, User, Product, Order, OrderItem, CartItem, ph_now
from flask_login import login_user, logout_user, current_user


def _turso_sync():
    """Fire-and-forget sync to Turso remote after a write.

    Runs in a background thread with a 10 s timeout so it NEVER blocks
    the response — a slow or dead Turso stream cannot cause a 504 timeout.
    """
    import threading

    def _do_sync():
        try:
            wrapper = db.engine.pool._creator()
            wrapper.sync()
            print("✅ _turso_sync complete")
        except Exception as e:
            print(f"⚠️  _turso_sync failed (non-fatal): {e}")

    t = threading.Thread(target=_do_sync, daemon=True)
    t.start()
    # Wait max 10 s — if Turso is slow we don't block the HTTP response
    t.join(timeout=10)

class AuthController:
    """Handles authentication operations"""
    
    @staticmethod
    def register_user(username, email, password, full_name, phone='', address=''):
        """Register a new user"""
        # Check if user exists
        if User.query.filter_by(username=username).first():
            return False, 'Username already exists'
        
        if User.query.filter_by(email=email).first():
            return False, 'Email already registered'
        
        # Create new user
        user = User(
            username=username,
            email=email,
            full_name=full_name,
            phone=phone,
            address=address,
            profile_complete=True
        )
        user.set_password(password)
        
        # First user becomes admin (optional)
        if not User.query.filter_by(is_admin=True).first():
            user.is_admin = True
        
        db.session.add(user)
        db.session.commit()
        
        return True, user
    
    @staticmethod
    def login_user(username_or_email, password, remember=False):
        """Login user"""
        user = User.query.filter(
            (User.username == username_or_email) | 
            (User.email == username_or_email)
        ).first()
        
        if user and user.check_password(password):
            if not getattr(user, 'is_active', True):
                return False, 'Your account has been disabled. Please contact support.'
            login_user(user, remember=remember)
            return True, user
        
        return False, 'Invalid username/email or password'
    
    @staticmethod
    def logout_user():
        """Logout user"""
        logout_user()
    
    @staticmethod
    def get_user_profile(user_id):
        """Get user profile with orders"""
        user = User.query.get(user_id)
        if user:
            orders = Order.query.filter_by(user_id=user_id).order_by(Order.order_date.desc()).all()
            return user, orders
        return None, None
    
    @staticmethod
    def update_profile(user_id, data):
        """Update user profile"""
        user = User.query.get(user_id)
        if user:
            user.full_name = data.get('full_name', user.full_name)
            user.phone = data.get('phone', user.phone)
            user.address = data.get('address', user.address)
            
            if 'email' in data and data['email'] != user.email:
                if User.query.filter_by(email=data['email']).first():
                    return False, 'Email already in use'
                user.email = data['email']
            
            db.session.commit()
            return True, user
        return False, 'User not found'
    
    @staticmethod
    def change_password(user_id, old_password, new_password):
        """Change user password"""
        user = User.query.get(user_id)
        if user and user.check_password(old_password):
            user.set_password(new_password)
            db.session.commit()
            return True, 'Password changed successfully'
        return False, 'Current password is incorrect'

class ProductController:
    """Handles product-related operations"""
    
    @staticmethod
    def get_all_products():
        """Get all available fries products"""
        return Product.query.filter_by(is_available=True, category='Fries').all()
    
    @staticmethod
    def get_product_by_id(product_id):
        """Get single product by ID"""
        return Product.query.get(product_id)
    
    @staticmethod
    def get_products_by_flavor(flavor):
        """Get products by flavor"""
        return Product.query.filter_by(flavor=flavor, is_available=True, category='Fries').all()
    
    @staticmethod
    def get_flavors():
        """Get all unique flavors from Fries only"""
        flavors = db.session.query(Product.flavor).filter_by(category='Fries', is_available=True).distinct().all()
        return [flavor[0] for flavor in flavors]

class CartController:
    """Handles shopping cart operations"""
    
    @staticmethod
    def get_cart_items(session_id, user_id=None, exclude_ids=None):
        """Get all active (non-ordered) cart items for the current user/session.

        is_ordered=True items are permanently hidden — this is the primary guard
        against Turso replication lag causing ordered items to reappear.
        exclude_ids is kept as a secondary session-level blacklist for the brief
        window before the DB write fully propagates.
        """
        if user_id:
            items = CartItem.query.filter_by(user_id=user_id, is_ordered=False).all()
        else:
            items = CartItem.query.filter(
                CartItem.session_id == session_id,
                CartItem.user_id == None,  # noqa: E711
                CartItem.is_ordered == False  # noqa: E712
            ).all()

        if exclude_ids:
            ex = set(exclude_ids)
            items = [i for i in items if i.id not in ex]
        return items
    
    @staticmethod
    def add_to_cart(session_id, product_id, user_id=None, quantity=1):
        """Add item to cart"""
        # IMPORTANT: only match rows that have NOT been ordered yet.
        # Without is_ordered=False, a re-add of the same product could
        # accidentally merge into an already-ordered (soft-deleted) row,
        # making it reappear in the cart even though it was cleared.
        if user_id:
            cart_item = CartItem.query.filter_by(
                product_id=product_id, user_id=user_id, is_ordered=False
            ).first()
        else:
            cart_item = CartItem.query.filter(
                CartItem.product_id == product_id,
                CartItem.session_id == session_id,
                CartItem.user_id == None,       # noqa: E711
                CartItem.is_ordered == False    # noqa: E712
            ).first()
        
        if cart_item:
            cart_item.quantity += quantity
        else:
            cart_item = CartItem(
                session_id=session_id if not user_id else None,
                user_id=user_id,
                product_id=product_id,
                quantity=quantity
            )
            db.session.add(cart_item)
        
        db.session.commit()
        return cart_item
    
    @staticmethod
    def update_quantity(session_id, item_id, quantity, user_id=None):
        """Update item quantity"""
        query = CartItem.query.filter_by(id=item_id)
        
        if user_id:
            cart_item = query.filter_by(user_id=user_id).first()
        else:
            cart_item = query.filter_by(session_id=session_id).first()
        
        if cart_item:
            if quantity <= 0:
                db.session.delete(cart_item)
            else:
                cart_item.quantity = quantity
            db.session.commit()
            return True
        return False
    
    @staticmethod
    def remove_from_cart(session_id, item_id, user_id=None):
        """Remove item from cart — matches by user_id OR session_id so
        items added as a guest and then merged (or partially merged) are
        always deletable after login."""
        from sqlalchemy import or_
        cart_item = CartItem.query.filter(
            CartItem.id == item_id,
            or_(
                CartItem.user_id    == user_id    if user_id    else False,
                CartItem.session_id == session_id if session_id else False,
            )
        ).first()

        # Last-resort: if neither matched but the item_id exists and the caller
        # IS authenticated, allow deletion (covers edge-cases after cart merge).
        if not cart_item and user_id:
            cart_item = CartItem.query.filter_by(id=item_id).first()

        if cart_item:
            db.session.delete(cart_item)
            db.session.commit()
            return True
        return False
    
    @staticmethod
    def clear_cart(session_id, user_id=None):
        """Clear ALL items from cart"""
        if user_id:
            CartItem.query.filter_by(user_id=user_id).delete()
        else:
            CartItem.query.filter_by(session_id=session_id).delete()
        db.session.commit()
    
    @staticmethod
    def clear_selected_items(session_id, user_id, item_ids):
        """Hard-delete the ordered cart rows and sync to Turso remote.

        Steps:
        1. Mark is_ordered=True  — immediately hides items from get_cart_items
           even if the hard DELETE is delayed by replication.
        2. Hard DELETE           — removes the rows permanently.
        3. _turso_sync()         — pushes both writes to the Turso remote so
                                   the next request always sees a clean cart.
        """
        if not item_ids:
            return

        try:
            CartItem.query.filter(
                CartItem.id.in_(item_ids)
            ).update({'is_ordered': True}, synchronize_session='fetch')
            db.session.commit()
            print(f"✅ clear_selected_items soft-deleted {len(item_ids)} rows")
        except Exception as e:
            db.session.rollback()
            print(f"⚠️  clear_selected_items soft-delete failed: {e}")
            return

        try:
            CartItem.query.filter(
                CartItem.id.in_(item_ids)
            ).delete(synchronize_session='fetch')
            db.session.commit()
            db.session.expire_all()
            print(f"✅ clear_selected_items hard-deleted {len(item_ids)} rows")
        except Exception as e:
            db.session.rollback()
            print(f"⚠️  clear_selected_items hard delete failed: {e}")

        # Push both writes to Turso remote immediately.
        _turso_sync()
    
    @staticmethod
    def get_cart_total(session_id, user_id=None):
        """Calculate cart total"""
        cart_items = CartController.get_cart_items(session_id, user_id)
        total = 0
        for item in cart_items:
            total += item.product.price * item.quantity
        return total
    
    @staticmethod
    def merge_carts(session_id, user_id):
        """Merge session cart with user cart on login.
        Only processes rows where is_ordered=False — ordered rows must never
        be revived or re-merged even if they still exist in the DB.
        """
        session_items = CartItem.query.filter_by(
            session_id=session_id, user_id=None, is_ordered=False
        ).all()
        user_items = CartItem.query.filter_by(
            user_id=user_id, is_ordered=False
        ).all()
        
        # Create dict of user items for quick lookup
        user_items_dict = {item.product_id: item for item in user_items}
        
        for session_item in session_items:
            if session_item.product_id in user_items_dict:
                # Merge: add session qty to existing user item
                user_items_dict[session_item.product_id].quantity += session_item.quantity
                db.session.delete(session_item)
            else:
                # Reassign session item to user and clear session link
                session_item.user_id = user_id
                session_item.session_id = None
        
        db.session.commit()

        # Purge any remaining orphaned session rows that still reference this
        # session_id but are now shadowed by a user-owned row (can happen if
        # merge_carts ran in a previous session but a stale cookie left rows
        # behind). This is the root cause of the badge showing double the count.
        orphans = CartItem.query.filter(
            CartItem.session_id == session_id,
            CartItem.user_id == None  # noqa: E711
        ).all()
        for orphan in orphans:
            db.session.delete(orphan)
        if orphans:
            db.session.commit()

class OrderController:
    """Handles order operations"""
    
    @staticmethod
    def generate_order_number():
        """Generate unique order number"""
        while True:
            date_part = ph_now().strftime('%Y%m%d')
            random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
            order_number = f"PC-{date_part}-{random_part}"
            
            if not Order.query.filter_by(order_number=order_number).first():
                return order_number
    
    @staticmethod
    def create_order(session_id, customer_data, cart_items, user_id=None, delivery_fee=50):
        """Create new order from cart"""
        order_number = OrderController.generate_order_number()
        subtotal = sum(item.product.price * item.quantity for item in cart_items)
        total = subtotal + delivery_fee
        
        order = Order(
            order_number=order_number,
            user_id=user_id,
            customer_name=customer_data['name'],
            customer_email=customer_data['email'],
            customer_phone=customer_data['phone'],
            delivery_address=customer_data['address'],
            delivery_lat=customer_data.get('delivery_lat'),
            delivery_lng=customer_data.get('delivery_lng'),
            total_amount=total,
            payment_method=customer_data['payment_method'],
            status='Pending',
            payment_status='Unpaid'
        )
        
        db.session.add(order)
        db.session.flush()
        
        for cart_item in cart_items:
            order_item = OrderItem(
                order_id=order.id,
                product_id=cart_item.product_id,
                product_name=cart_item.product.name,
                quantity=cart_item.quantity,
                price=cart_item.product.price,
                subtotal=cart_item.product.price * cart_item.quantity
            )
            db.session.add(order_item)
        
        # Commit everything in one transaction. For libsql/Turso, the
        # _LibSQLConnection.commit() wrapper calls conn.sync() automatically
        # so the write is pushed to Turso before we redirect.
        db.session.commit()
        _turso_sync()   # force push to Turso remote — makes order visible immediately
        return order
    
    @staticmethod
    def get_order(order_number):
        """Get order by order number"""
        return Order.query.filter_by(order_number=order_number).first()
    
    @staticmethod
    def get_user_orders(user_id):
        """Get all orders for a user"""
        return Order.query.filter_by(user_id=user_id).order_by(Order.order_date.desc()).all()
    
    @staticmethod
    def get_all_orders():
        """Get all orders (for admin)"""
        return Order.query.order_by(Order.order_date.desc()).all()
    
    @staticmethod
    def update_order_status(order_id, status):
        """Update order status"""
        order = Order.query.get(order_id)
        if order:
            order.status = status
            db.session.commit()
            _turso_sync()   # push status change to Turso remote immediately
            return True
        return False