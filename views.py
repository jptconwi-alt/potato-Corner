import os
import uuid
from flask import render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required, current_user, logout_user
from datetime import datetime
from werkzeug.utils import secure_filename
from models import db, User, Product, Order, OrderItem, CartItem
from controllers import AuthController, ProductController, CartController, OrderController
from auth_decorator import admin_required
import paymongo

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
UPLOAD_FOLDER = os.path.join('static', 'images', 'uploads')


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_session_id():
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    return session['session_id']


def register_routes(app):
    try:
        os.makedirs(os.path.join(app.root_path, UPLOAD_FOLDER), exist_ok=True)
    except OSError:
        pass  # Read-only filesystem (Vercel) — file uploads via disk unavailable

    # ─────────────────────────────────────────
    # PUBLIC ROUTES
    # ─────────────────────────────────────────

    @app.route('/')
    def index():
        products = ProductController.get_all_products()
        flavors = ProductController.get_flavors()
        return render_template('index.html', products=products, flavors=flavors)

    # ─────────────────────────────────────────
    # AUTH ROUTES
    # ─────────────────────────────────────────

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            if current_user.is_admin:
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('index'))
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            remember = bool(request.form.get('remember'))
            success, result = AuthController.login_user(username, password, remember)
            if success:
                session['user_id'] = result.id
                sid = get_session_id()
                CartController.merge_carts(sid, result.id)
                flash(f'Welcome back, {result.full_name}! 🍟', 'success')
                if result.is_admin:
                    return redirect(url_for('admin_dashboard'))
                return redirect(url_for('index'))
            else:
                flash(result, 'danger')
        return render_template('login.html')

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for('index'))
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '')
            confirm = request.form.get('confirm_password', '')
            full_name = request.form.get('full_name', '').strip()
            phone = request.form.get('phone', '').strip()
            street = request.form.get('street', '').strip()
            barangay = request.form.get('barangay', '').strip()
            city = request.form.get('city', '').strip()
            province = request.form.get('province', '').strip()
            zipcode = request.form.get('zipcode', '').strip()

            if password != confirm:
                flash('Passwords do not match', 'danger')
                return render_template('register.html')
            if len(password) < 6:
                flash('Password must be at least 6 characters', 'danger')
                return render_template('register.html')

            success, result = AuthController.register_user(username, email, password, full_name, phone)
            if success:
                # Save address fields
                result.street = street
                result.barangay = barangay
                result.city = city
                result.province = province
                result.zipcode = zipcode
                result.profile_complete = True
                db.session.commit()
                flash('Account created! Please log in.', 'success')
                return redirect(url_for('login'))
            else:
                flash(result, 'danger')
        return render_template('register.html')

    @app.route('/logout')
    def logout():
        logout_user()          # Flask-Login: clears user, queues remember cookie deletion
        session.pop('user_id', None)   # remove only our custom key, NOT session.clear()
        session.modified = True
        flash('You have been logged out.', 'info')
        response = redirect(url_for('login'))
        # Explicitly expire the remember_token cookie that Flask-Login sets
        response.delete_cookie('remember_token')
        return response

    @app.route('/complete-profile', methods=['GET', 'POST'])
    @login_required
    def complete_profile():
        if request.method == 'POST':
            current_user.full_name = request.form.get('full_name', current_user.full_name).strip()
            current_user.phone = request.form.get('phone', '').strip()
            current_user.street = request.form.get('street', '').strip()
            current_user.barangay = request.form.get('barangay', '').strip()
            current_user.city = request.form.get('city', '').strip()
            current_user.province = request.form.get('province', '').strip()
            current_user.zipcode = request.form.get('zipcode', '').strip()
            current_user.profile_complete = True
            db.session.commit()
            flash('Profile complete! Start ordering.', 'success')
            return redirect(url_for('index'))
        return render_template('complete_profile.html')

    # ─────────────────────────────────────────
    # PROFILE & ORDERS
    # ─────────────────────────────────────────

    @app.route('/profile', methods=['GET', 'POST'])
    @login_required
    def profile():
        orders = OrderController.get_user_orders(current_user.id)
        if request.method == 'POST':
            current_user.full_name = request.form.get('full_name', current_user.full_name).strip()
            current_user.phone = request.form.get('phone', current_user.phone or '').strip()
            current_user.street = request.form.get('street', current_user.street or '').strip()
            current_user.barangay = request.form.get('barangay', current_user.barangay or '').strip()
            current_user.city = request.form.get('city', current_user.city or '').strip()
            new_email = request.form.get('email', '').strip()
            if new_email and new_email != current_user.email:
                if User.query.filter_by(email=new_email).first():
                    flash('Email already in use', 'danger')
                    return render_template('profile.html', orders=orders)
                current_user.email = new_email
            db.session.commit()
            flash('Profile updated!', 'success')
        return render_template('profile.html', orders=orders)

    @app.route('/orders')
    @login_required
    def orders():
        user_orders = OrderController.get_user_orders(current_user.id)
        return render_template('orders.html', orders=user_orders)

    @app.route('/my-orders')
    @login_required
    def my_orders():
        user_orders = OrderController.get_user_orders(current_user.id)
        return render_template('my_orders.html', orders=user_orders)

    @app.route('/track-order')
    def track_order():
        order_number = request.args.get('order_number', '').strip()
        order = None
        if order_number:
            order = OrderController.get_order(order_number)
            if not order:
                flash('Order not found. Please check the order number.', 'danger')
        return render_template('track_order.html', order=order, order_number=order_number)

    # ─────────────────────────────────────────
    # CART ROUTES
    # ─────────────────────────────────────────

    @app.route('/cart')
    def cart():
        sid = get_session_id()
        uid = current_user.id if current_user.is_authenticated else None
        cart_items = CartController.get_cart_items(sid, uid)
        total = sum(i.product.price * i.quantity for i in cart_items)
        return render_template('cart.html', cart_items=cart_items, total=total)

    @app.route('/cart/add', methods=['POST'])
    def cart_add():
        data = request.get_json()
        product_id = data.get('product_id')
        quantity = int(data.get('quantity', 1))
        product = Product.query.get(product_id)
        if not product or not product.is_available:
            return jsonify({'success': False, 'message': 'Product not available'})
        sid = get_session_id()
        uid = current_user.id if current_user.is_authenticated else None
        CartController.add_to_cart(sid, product_id, uid, quantity)
        return jsonify({'success': True, 'message': 'Added to cart'})

    @app.route('/cart/update', methods=['POST'])
    def cart_update():
        data = request.get_json()
        item_id = data.get('item_id')
        quantity = int(data.get('quantity', 1))
        sid = get_session_id()
        uid = current_user.id if current_user.is_authenticated else None
        CartController.update_quantity(sid, item_id, quantity, uid)
        return jsonify({'success': True})

    @app.route('/cart/remove', methods=['POST'])
    def cart_remove():
        data = request.get_json()
        item_id = data.get('item_id')
        sid = get_session_id()
        uid = current_user.id if current_user.is_authenticated else None
        CartController.remove_from_cart(sid, item_id, uid)
        return jsonify({'success': True})

    @app.route('/api/cart/count')
    def cart_count():
        sid = get_session_id()
        uid = current_user.id if current_user.is_authenticated else None
        cart_items = CartController.get_cart_items(sid, uid)
        total = sum(i.quantity for i in cart_items)
        return jsonify({'count': total})

    # ─────────────────────────────────────────
    # CHECKOUT & ORDERS
    # ─────────────────────────────────────────

    @app.route('/checkout', methods=['GET', 'POST'])
    def checkout():
        sid = get_session_id()
        uid = current_user.id if current_user.is_authenticated else None
        cart_items = CartController.get_cart_items(sid, uid)
        if not cart_items:
            flash('Your cart is empty', 'warning')
            return redirect(url_for('cart'))
        total = sum(i.product.price * i.quantity for i in cart_items)

        if request.method == 'POST':
            customer_data = {
                'name':           request.form.get('name', '').strip(),
                'email':          request.form.get('email', '').strip(),
                'phone':          request.form.get('phone', '').strip(),
                'address':        request.form.get('address', '').strip(),
                'payment_method': request.form.get('payment_method', 'Cash on Delivery'),
            }
            if not all([customer_data['name'], customer_data['email'],
                        customer_data['phone'], customer_data['address']]):
                flash('Please fill in all required fields', 'danger')
                return render_template('checkout.html', cart_items=cart_items, total=total)

            order = OrderController.create_order(sid, customer_data, cart_items, uid)

            # ── PayMongo e-wallet payment (GCash / Maya) ──────────────────
            if customer_data['payment_method'] in ('GCash', 'Maya'):
                try:
                    success_url = url_for('payment_success',
                                          order_number=order.order_number,
                                          _external=True)
                    failed_url  = url_for('payment_failed',
                                          order_number=order.order_number,
                                          _external=True)
                    source = paymongo.create_source(
                        order_number=order.order_number,
                        amount_php=order.total_amount,
                        payment_method=customer_data['payment_method'],
                        success_url=success_url,
                        failed_url=failed_url,
                    )
                    # Save source ID for later verification
                    order.paymongo_source_id = source['id']
                    db.session.commit()

                    CartController.clear_cart(sid, uid)
                    # Redirect user to GCash / Maya checkout page
                    checkout_url = source['attributes']['redirect']['checkout_url']
                    return redirect(checkout_url)
                except Exception as e:
                    db.session.delete(order)
                    db.session.commit()
                    flash(f'Payment setup failed: {str(e)}. Please try again or use Cash on Delivery.', 'danger')
                    return render_template('checkout.html', cart_items=cart_items, total=total)

            # ── Cash on Delivery ──────────────────────────────────────────
            CartController.clear_cart(sid, uid)
            return redirect(url_for('order_confirmation', order_number=order.order_number))

        return render_template('checkout.html', cart_items=cart_items, total=total)

    @app.route('/payment/success/<order_number>')
    def payment_success(order_number):
        """User returns here after completing GCash/Maya payment."""
        order = OrderController.get_order(order_number)
        if not order:
            flash('Order not found.', 'danger')
            return redirect(url_for('index'))

        # Verify payment with PayMongo
        if order.paymongo_source_id and order.payment_status != 'Paid':
            try:
                source = paymongo.get_source(order.paymongo_source_id)
                status = source['attributes']['status']

                if status == 'chargeable':
                    payment = paymongo.create_payment(
                        source_id=order.paymongo_source_id,
                        amount_php=order.total_amount,
                        order_number=order.order_number,
                    )
                    paid_status = payment['attributes'].get('status', '')
                    if paid_status == 'paid':
                        order.payment_status = 'Paid'
                        order.status = 'Confirmed'
                        db.session.commit()
                elif status == 'paid':
                    order.payment_status = 'Paid'
                    order.status = 'Confirmed'
                    db.session.commit()
            except Exception as e:
                print(f'PayMongo verify error: {e}')

        return render_template('order_confirmation.html', order=order)

    @app.route('/payment/failed/<order_number>')
    def payment_failed(order_number):
        """User returns here if they cancel or payment fails."""
        order = OrderController.get_order(order_number)
        if order:
            order.payment_status = 'Failed'
            db.session.commit()
        flash('Payment was cancelled or failed. You can retry below or choose Cash on Delivery.', 'warning')
        return render_template('payment_failed.html', order=order)

    @app.route('/payment/retry/<order_number>')
    def retry_payment(order_number):
        """Re-initiate PayMongo payment for a failed/cancelled order."""
        order = OrderController.get_order(order_number)
        if not order or order.payment_status == 'Paid':
            return redirect(url_for('index'))
        try:
            success_url = url_for('payment_success', order_number=order.order_number, _external=True)
            failed_url  = url_for('payment_failed',  order_number=order.order_number, _external=True)
            source = paymongo.create_source(
                order_number=order.order_number,
                amount_php=order.total_amount,
                payment_method=order.payment_method,
                success_url=success_url,
                failed_url=failed_url,
            )
            order.paymongo_source_id = source['id']
            order.payment_status = 'Unpaid'
            db.session.commit()
            return redirect(source['attributes']['redirect']['checkout_url'])
        except Exception as e:
            flash(f'Could not initiate payment: {str(e)}', 'danger')
            return redirect(url_for('payment_failed', order_number=order.order_number))

    @app.route('/order/confirmation/<order_number>')
    def order_confirmation(order_number):
        order = OrderController.get_order(order_number)
        if not order:
            flash('Order not found', 'danger')
            return redirect(url_for('index'))
        return render_template('order_confirmation.html', order=order)

    # ─────────────────────────────────────────
    # ADMIN ROUTES
    # ─────────────────────────────────────────

    @app.route('/admin')
    @admin_required
    def admin_dashboard():
        all_orders = Order.query.all()
        stats = {
            'total_orders': len(all_orders),
            'pending_orders': sum(1 for o in all_orders if o.status == 'Pending'),
            'delivered_orders': sum(1 for o in all_orders if o.status == 'Delivered'),
            'out_for_delivery': sum(1 for o in all_orders if o.status == 'Out for Delivery'),
            'total_revenue': sum(o.total_amount for o in all_orders if o.status == 'Delivered'),
            'total_users': User.query.count(),
        }
        recent_orders = Order.query.order_by(Order.order_date.desc()).limit(8).all()
        pending_delivery = Order.query.filter(
            Order.status.in_(['Pending', 'Preparing', 'Out for Delivery'])
        ).order_by(Order.order_date.asc()).limit(5).all()
        pending_count = stats['pending_orders']
        return render_template('admin/dashboard.html',
                               stats=stats,
                               recent_orders=recent_orders,
                               pending_delivery=pending_delivery,
                               pending_count=pending_count,
                               now=datetime.utcnow())

    @app.route('/admin/orders')
    @admin_required
    def admin_orders():
        all_orders = Order.query.order_by(Order.order_date.desc()).all()
        return render_template('admin/orders.html', orders=all_orders)

    @app.route('/admin/users')
    @admin_required
    def admin_users():
        all_users = User.query.order_by(User.created_at.desc()).all()
        return render_template('admin/users.html', users=all_users)

    @app.route('/admin/products')
    @admin_required
    def admin_products():
        all_products = Product.query.order_by(Product.flavor, Product.size).all()
        return render_template('admin/products.html', products=all_products)

    # ── Admin AJAX: Update order status ─────

    @app.route('/admin/order/<int:order_id>/status', methods=['POST'])
    @admin_required
    def admin_update_order_status(order_id):
        data = request.get_json()
        new_status = data.get('status')
        valid = ['Pending', 'Preparing', 'Out for Delivery', 'Delivered', 'Cancelled']
        if new_status not in valid:
            return jsonify({'success': False, 'message': 'Invalid status'})
        success = OrderController.update_order_status(order_id, new_status)
        return jsonify({'success': success})

    # ── Admin AJAX: Update payment status ──────

    @app.route('/admin/order/<int:order_id>/payment', methods=['POST'])
    @admin_required
    def admin_update_payment_status(order_id):
        from models import Order
        data = request.get_json()
        new_status = data.get('payment_status')
        if new_status not in ['Paid', 'Unpaid']:
            return jsonify({'success': False, 'message': 'Invalid payment status'})
        order = Order.query.get(order_id)
        if not order:
            return jsonify({'success': False, 'message': 'Order not found'})
        order.payment_status = new_status
        db.session.commit()
        return jsonify({'success': True})

    # ── Admin AJAX: Get user orders ─────────

    @app.route('/admin/user/<int:user_id>/orders')
    @admin_required
    def admin_user_orders(user_id):
        user_orders = Order.query.filter_by(user_id=user_id).order_by(Order.order_date.desc()).all()
        data = []
        for o in user_orders:
            data.append({
                'order_number': o.order_number,
                'status': o.status,
                'total_amount': o.total_amount,
                'order_date': o.order_date.strftime('%b %d, %Y %I:%M %p'),
                'items': [{'product_name': i.product_name, 'quantity': i.quantity} for i in o.items]
            })
        return jsonify({'orders': data})

    # ── Admin AJAX: Upload product image (base64 stored in DB) ────

    @app.route('/admin/product/upload-image', methods=['POST'])
    @admin_required
    def admin_upload_product_image():
        import base64
        product_id = request.form.get('product_id')
        product = Product.query.get(product_id)
        if not product:
            return jsonify({'success': False, 'message': 'Product not found'})

        file = request.files.get('image')
        if not file or not file.filename:
            return jsonify({'success': False, 'message': 'No file uploaded'})
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'message': 'Invalid file type'})

        ext = file.filename.rsplit('.', 1)[1].lower()
        mime = 'image/svg+xml' if ext == 'svg' else f'image/{ext}'
        img_bytes = file.read()
        if len(img_bytes) > 5 * 1024 * 1024:
            return jsonify({'success': False, 'message': 'Image too large (max 5MB)'})

        b64 = base64.b64encode(img_bytes).decode('utf-8')
        data_url = f"data:{mime};base64,{b64}"

        product.image_data = data_url
        product.image_url = f"_data_"   # sentinel: use image_data
        db.session.commit()

        return jsonify({'success': True, 'image_url': data_url})

    # ── Admin AJAX: Toggle product availability ─

    @app.route('/admin/product/<int:product_id>/toggle', methods=['POST'])
    @admin_required
    def admin_toggle_product(product_id):
        product = Product.query.get(product_id)
        if not product:
            return jsonify({'success': False})
        data = request.get_json()
        product.is_available = data.get('available', True)
        db.session.commit()
        return jsonify({'success': True})

    # ── Admin: Add new product ───────────────

    @app.route('/admin/product/add', methods=['POST'])
    @admin_required
    def admin_add_product():
        import base64
        name = request.form.get('name', '').strip()
        price = request.form.get('price')
        flavor = request.form.get('flavor', '').strip()
        size = request.form.get('size', '').strip()
        description = request.form.get('description', '').strip()
        category = request.form.get('category', 'Fries').strip()

        if not all([name, price, flavor, size, description]):
            missing = [k for k,v in {'name':name,'price':price,'flavor':flavor,'size':size,'description':description}.items() if not v]
            return jsonify({'success': False, 'message': f'Missing fields: {", ".join(missing)}'})

        try:
            price = float(price)
        except (ValueError, TypeError):
            return jsonify({'success': False, 'message': 'Invalid price'})

        image_url = 'fries/cheese.svg'
        image_data = None

        file = request.files.get('image')
        if file and file.filename and allowed_file(file.filename):
            ext = file.filename.rsplit('.', 1)[1].lower()
            mime = 'image/svg+xml' if ext == 'svg' else f'image/{ext}'
            img_bytes = file.read()
            if len(img_bytes) <= 5 * 1024 * 1024:
                b64 = base64.b64encode(img_bytes).decode('utf-8')
                image_data = f"data:{mime};base64,{b64}"
                image_url = '_data_'

        product = Product(
            name=name, price=price, flavor=flavor, size=size,
            description=description, image_url=image_url,
            image_data=image_data, is_available=True, category=category
        )
        db.session.add(product)
        db.session.commit()

        return jsonify({'success': True, 'product_id': product.id})