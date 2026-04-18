from flask import Flask, redirect, url_for, session, flash
from models import db
from views import register_routes
from init_db import init_database
from flask_login import LoginManager, login_user
from models import User
from authlib.integrations.flask_client import OAuth
import os
from dotenv import load_dotenv

load_dotenv(override=False)
login_manager = LoginManager()


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY']                  = os.environ.get('SECRET_KEY', 'potato-corner-secret-2025')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['GOOGLE_CLIENT_ID']            = os.environ.get('GOOGLE_CLIENT_ID', '')
    app.config['GOOGLE_CLIENT_SECRET']        = os.environ.get('GOOGLE_CLIENT_SECRET', '')

    # ── Database: Turso (libsql) or fallback to /tmp SQLite ──────────────────
    turso_url   = os.environ.get('TURSO_DATABASE_URL', 'libsql://potata-corner-jpconwi.aws-ap-northeast-1.turso.io')
    turso_token = os.environ.get('TURSO_AUTH_TOKEN', 'eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3NzY0OTk2MDMsImlkIjoiMDE5ZDk4ZWUtZjMwMS03OTk2LTg5MDItY2M2NzA1OGY5MzhhIiwicmlkIjoiYjRkMjcwNzAtYjUyOS00Y2Q4LTgxZDMtNjY2MDEyNzFjODZlIn0.dwyLCvtATpp9_5MhsSPT0e7jIfBSCFwvYS4MrbrvRH7wOjmuPITbm1Cj6zQkIJTDakg6fFVy1qCF_A6bzDcUCQ')
    if turso_url and turso_token:
        db_uri = turso_url.replace('libsql://', 'sqlite+libsql://') \
                          .replace('https://', 'sqlite+libsql://')
        db_uri = f"{db_uri}?authToken={turso_token}&secure=true"
    else:
        # Local dev fallback — /tmp avoids read-only FS issues on Vercel
        db_uri = 'sqlite:////tmp/potato_corner.db'

    app.config['SQLALCHEMY_DATABASE_URI'] = db_uri

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'warning'

    oauth = OAuth(app)
    google = None
    if app.config['GOOGLE_CLIENT_ID'] and app.config['GOOGLE_CLIENT_SECRET']:
        google = oauth.register(
            name='google',
            client_id=app.config['GOOGLE_CLIENT_ID'],
            client_secret=app.config['GOOGLE_CLIENT_SECRET'],
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_kwargs={'scope': 'openid email profile', 'prompt': 'select_account'}
        )

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    with app.app_context():
        try:
            db.create_all()
            from init_db import run_migrations
            run_migrations(db.engine)
            init_database()
        except Exception as e:
            print(f"❌ DB error: {e}")
            import traceback; traceback.print_exc()

    register_routes(app)

    # ── Google OAuth ─────────────────────────────────────────
    @app.route('/login/google')
    def google_login():
        if google is None:
            flash('Google login is not configured. Please use email/password.', 'warning')
            return redirect(url_for('login'))
        redirect_uri = url_for('google_authorize', _external=True)
        return google.authorize_redirect(redirect_uri)

    @app.route('/login/google/callback')
    def google_authorize():
        if google is None:
            flash('Google login is not configured.', 'danger')
            return redirect(url_for('login'))
        try:
            token = google.authorize_access_token()
            try:
                user_info = google.userinfo()
            except Exception:
                user_info = token.get('userinfo') or {}

            google_id = user_info.get('sub')
            email     = user_info.get('email')
            name      = user_info.get('name', '')

            if not email:
                flash('Could not retrieve email from Google.', 'danger')
                return redirect(url_for('login'))

            user = User.query.filter_by(email=email).first()
            if not user and google_id:
                user = User.query.filter_by(google_id=google_id).first()

            is_new = False
            if not user:
                is_new = True
                username = email.split('@')[0]
                if User.query.filter_by(username=username).first():
                    username = f"{username}_{google_id[:6] if google_id else 'g'}"
                import secrets
                user = User(
                    username=username, email=email,
                    full_name=name or username,
                    google_id=google_id, is_admin=False,
                    profile_complete=False
                )
                user.set_password(secrets.token_urlsafe(16))
                db.session.add(user)
                db.session.commit()
            else:
                changed = False
                if google_id and not user.google_id:
                    user.google_id = google_id; changed = True
                if name and user.full_name != name:
                    user.full_name = name; changed = True
                if changed:
                    db.session.commit()

            login_user(user, remember=False)
            session['user_id'] = user.id

            if is_new or not user.profile_complete:
                flash('Welcome! Please complete your profile so we can deliver your order.', 'warning')
                return redirect(url_for('complete_profile'))

            flash(f'Welcome back, {user.full_name}! 🍟', 'success')
            next_page = session.pop('next', None)
            return redirect(next_page or url_for('index'))

        except Exception as e:
            flash(f'Google login failed: {str(e)}', 'danger')
            return redirect(url_for('login'))

    return app


# Create the global app instance for Gunicorn
app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"🚀 Starting Potato Corner at http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)