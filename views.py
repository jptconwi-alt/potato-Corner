from flask import render_template, request, jsonify, session, redirect, url_for, flash
from models import db
from controllers import ProductController, CartController, OrderController, AuthController
from auth_decorator import login_required, admin_required
from flask_login import current_user
import uuid

FOOD_IMAGES = {
    "Cheese":        "https://images.unsplash.com/photo-1573080496219-bb080dd4f877?w=400&q=80",
    "Sour Cream":    "https://images.unsplash.com/photo-1630384060421-cb20d0e0649d?w=400&q=80",
    "BBQ":           "https://images.unsplash.com/photo-1568901346375-23c9450c58cd?w=400&q=80",
    "Chili BBQ":     "https://images.unsplash.com/photo-1619881590738-a111d176d906?w=400&q=80",
    "Wasabi":        "https://images.unsplash.com/photo-1541592106381-b31e9677c0e5?w=400&q=80",
    "White Cheddar": "https://images.unsplash.com/photo-1553979459-d2229ba7433b?w=400&q=80",
    "Chili Powder":  "https://images.unsplash.com/photo-1596649299486-4cdea56fd59d?w=400&q=80",
    "Salted Caramel":"https://images.unsplash.com/photo-1518013431117-eb1465fa5752?w=400&q=80",
}

def register_routes(app):

    @app.context_processor
    def inject_globals():
        return dict(FOOD_IMAGES=FOOD_IMAGES)

    # ── Root ──────────────────────────────────────────────
    @app.route('/')
    def root():
        if current_user.is_authenticated:
            return redirect(url_for('index'))
        return redirect(url_for('login'))

    @app.route('/menu')
    @login_required
    def index():
        # Banner if profile incomplete
        needs_profile = not current_user.profile_complete
        products = ProductController.get_all_products()
        flavors  = ProductController.get_flavors()
        return render_template('index.html', products=products, flavors=flavors, needs_profile=needs_profile)

    # ── AUTH ──────────────────────────────────────────────
    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for('index'))
        if request.method == 'POST':
            success, result = AuthController.register_user(
                request.form['username'], request.form['email'],
                request.form['password'], request.form['full_name'],
                request.form.get('phone', ''), request.form.get('address', '')
            )
            if success:
                flash('Registration successful! Please log in.', 'success')
                return redirect(url_for('login'))
            flash(result, 'danger')
        return render_template('register.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('index'))
        if request.method == 'POST':
            success, result = AuthController.login_user(
                request.form['username_or_email'],
                request.form['password'],
                'remember' in request.form
            )
            if success:
                if 'cart_id' in session:
                    CartController.merge_carts(session['cart_id'], result.id)
                flash(f'Welcome back, {result.full_name}! 🍟', 'success')
                next_page = request.args.get('next')
                return redirect(next_page or url_for('index'))
            flash(result, 'danger')
        return render_template('login.html')

    @app.route('/logout')
    @login_required
    def logout():
        AuthController.logout_user()
        flash('You have been logged out.', 'info')
        return redirect(url_for('login'))

    # ── Complete Profile (Google new users) ───────────────
    @app.route('/complete-profile', methods=['GET', 'POST'])
    @login_required
    def complete_profile():
        if request.method == 'POST':
            from models import User
            user = User.query.get(current_user.id)
            user.full_name  = request.form.get('full_name', user.full_name).strip()
            user.phone      = request.form.get('phone', '').strip()
            user.street     = request.form.get('street', '').strip()
            user.barangay   = request.form.get('barangay', '').strip()
            user.city       = request.form.get('city', '').strip()
            user.province   = request.form.get('province', '').strip()
            user.zipcode    = request.form.get('zipcode', '').strip()
            user.address    = user.get_full_address()
            user.profile_complete = True
            db.session.commit()
            flash('Profile completed! Welcome to Potato Corner! 🍟', 'success')
            return redirect(url_for('index'))
        return render_template('complete_profile.html')

    # ── Profile ───────────────────────────────────────────
    @app.route('/profile')
    @login_required
    def profile():
        user, orders = AuthController.get_user_profile(current_user.id)
        return render_template('profile.html', user=user, orders=orders)

    @app.route('/profile/update', methods=['POST'])
    @login_required
    def update_profile():
        from models import User
        user = User.query.get(current_user.id)
        user.full_name  = request.form.get('full_name', user.full_name)
        user.phone      = request.form.get('phone', user.phone)
        user.street     = request.form.get('street', user.street)
        user.barangay   = request.form.get('barangay', user.barangay)
        user.city       = request.form.get('city', user.city)
        user.province   = request.form.get('province', user.province)
        user.zipcode    = request.form.get('zipcode', user.zipcode)
        new_email = request.form.get('email', user.email)
        from models import User as U
        if new_email != user.email and U.query.filter_by(email=new_email).first():
            flash('Email already in use.', 'danger')
            return redirect(url_for('profile'))
        user.email   = new_email
        user.address = user.get_full_address()
        user.profile_complete = True
        db.session.commit()
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile'))

    @app.route('/change-password', methods=['POST'])
    @login_required
    def change_password():
        success, message = AuthController.change_password(
            current_user.id,
            request.form['old_password'],
            request.form['new_password']
        )
        flash(message, 'success' if success else 'danger')
        return redirect(url_for('profile'))

    # ── Products ──────────────────────────────────────────
    @app.route('/api/products')
    @login_required
    def api_products():
        products = ProductController.get_all_products()
        return jsonify([p.to_dict() for p in products])

    # ── Cart ──────────────────────────────────────────────
    @app.route('/add-to-cart', methods=['POST'])
    @login_required
    def add_to_cart():
        data = request.json
        if 'cart_id' not in session:
            session['cart_id'] = str(uuid.uuid4())
        CartController.add_to_cart(
            session_id=session['cart_id'],
            user_id=current_user.id,
            product_id=data['product_id'],
            quantity=data.get('quantity', 1)
        )
        items = CartController.get_cart_items(session['cart_id'], current_user.id)
        return jsonify({'success': True, 'cart_count': len(items), 'message': 'Added to cart! 🍟'})

    @app.route('/cart')
    @login_required
    def view_cart():
        items = CartController.get_cart_items(session.get('cart_id'), current_user.id)
        total = CartController.get_cart_total(session.get('cart_id'), current_user.id)
        return render_template('cart.html', cart_items=items, total=total)

    @app.route('/update-cart', methods=['POST'])
    @login_required
    def update_cart():
        data = request.json
        success = CartController.update_quantity(
            session_id=session.get('cart_id'), user_id=current_user.id,
            item_id=data['item_id'], quantity=data['quantity']
        )
        if success:
            items = CartController.get_cart_items(session.get('cart_id'), current_user.id)
            total = CartController.get_cart_total(session.get('cart_id'), current_user.id)
            return jsonify({'success': True, 'cart_count': len(items), 'total': total})
        return jsonify({'success': False})

    @app.route('/remove-from-cart/<int:item_id>', methods=['DELETE'])
    @login_required
    def remove_from_cart(item_id):
        success = CartController.remove_from_cart(
            session_id=session.get('cart_id'), user_id=current_user.id, item_id=item_id
        )
        return jsonify({'success': success})

    @app.route('/api/cart-count')
    @login_required
    def cart_count():
        items = CartController.get_cart_items(session.get('cart_id'), current_user.id)
        return jsonify({'count': len(items)})

    # ── Checkout ──────────────────────────────────────────
    @app.route('/checkout', methods=['GET', 'POST'])
    @login_required
    def checkout():
        cart_items = CartController.get_cart_items(session.get('cart_id'), current_user.id)
        if not cart_items:
            flash('Your cart is empty.', 'warning')
            return redirect(url_for('view_cart'))

        if request.method == 'POST':
            customer_data = {
                'name':           request.form['name'],
                'email':          request.form['email'],
                'phone':          request.form['phone'],
                'address':        request.form['address'],
                'payment_method': request.form['payment_method']
            }
            order = OrderController.create_order(
                session_id=session.get('cart_id'), user_id=current_user.id,
                customer_data=customer_data, cart_items=cart_items
            )
            CartController.clear_cart(session.get('cart_id'), current_user.id)
            flash('Order placed successfully! 🎉', 'success')
            return redirect(url_for('order_confirmation', order_number=order.order_number))

        user_data = {
            'name':    current_user.full_name,
            'email':   current_user.email,
            'phone':   current_user.phone or '',
            'address': current_user.get_full_address() if hasattr(current_user, 'get_full_address') else (current_user.address or '')
        }
        total = CartController.get_cart_total(session.get('cart_id'), current_user.id)
        return render_template('checkout.html', cart_items=cart_items, total=total, user=user_data)

    @app.route('/order/<order_number>')
    @login_required
    def order_confirmation(order_number):
        order = OrderController.get_order(order_number)
        if not order or (order.user_id != current_user.id and not current_user.is_admin):
            flash('Order not found.', 'danger')
            return redirect(url_for('index'))
        return render_template('orders.html', order=order, track_mode=False)

    @app.route('/my-orders')
    @login_required
    def my_orders():
        orders = OrderController.get_user_orders(current_user.id)
        return render_template('my_orders.html', orders=orders)

    @app.route('/track-order')
    @login_required
    def track_order():
        order_number = request.args.get('order_number', '')
        order = None
        if order_number:
            order = OrderController.get_order(order_number)
            if order and order.user_id != current_user.id and not current_user.is_admin:
                flash('You can only track your own orders.', 'warning')
                return redirect(url_for('track_order'))
        return render_template('track_order.html', order=order, track_mode=True)

    # ── Admin ─────────────────────────────────────────────
    @app.route('/admin')
    @admin_required
    def admin_dashboard():
        orders = OrderController.get_all_orders()
        return render_template('admin.html', orders=orders)

    @app.route('/admin/update-order/<int:order_id>', methods=['POST'])
    @admin_required
    def admin_update_order(order_id):
        data = request.json
        success = OrderController.update_order_status(order_id, data['status'])
        return jsonify({'success': success})
