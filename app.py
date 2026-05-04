import warnings
import threading
import sqlite3
import os
from datetime import timedelta

warnings.filterwarnings('ignore', category=DeprecationWarning, module='authlib')

from flask import Flask, redirect, url_for, session, flash
from models import db
from views import register_routes
from init_db import init_database
from flask_login import LoginManager, login_user
from models import User
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv

load_dotenv()
login_manager = LoginManager()

# ── Turso background sync ─────────────────────────────────────────────────────
# All reads/writes go through plain sqlite3 on the local replica file.
# After every write we fire a background thread that opens a libsql connection,
# pushes the local changes to Turso remote, then closes it. This means:
#   • Zero network calls in the request/response path → no 504 timeouts
#   • Data is eventually consistent with Turso remote (usually within 1–2 s)

_LOCAL_DB = '/tmp/potato_corner_replica.db'
_turso_sync_lock = threading.Lock()


def turso_push_sync():
    """Push the local sqlite3 DB to Turso remote in a background thread.
    Called after every write. Never blocks the HTTP response."""
    turso_url   = os.environ.get('TURSO_DATABASE_URL', '')
    turso_token = os.environ.get('TURSO_AUTH_TOKEN', '')
    if not (turso_url and turso_token):
        return  # local-only mode, nothing to sync

    def _do():
        with _turso_sync_lock:
            try:
                import libsql_experimental as libsql
                sync_url = (turso_url
                            .replace('libsql://', 'https://')
                            .replace('sqlite+libsql://', 'https://'))
                conn = libsql.connect(
                    database=_LOCAL_DB,
                    sync_url=sync_url,
                    auth_token=turso_token,
                )
                conn.sync()
                conn.close()
                print('✅ Turso push sync complete')
            except Exception as e:
                print(f'⚠️  Turso push sync failed (non-fatal): {e}')

    threading.Thread(target=_do, daemon=True).start()


def turso_pull_sync():
    """Pull latest data from Turso remote into the local replica.
    Called once at cold start in a background thread.
    Never blocks the request path."""
    turso_url   = os.environ.get('TURSO_DATABASE_URL', '')
    turso_token = os.environ.get('TURSO_AUTH_TOKEN', '')
    if not (turso_url and turso_token):
        return

    def _do():
        try:
            import libsql_experimental as libsql
            sync_url = (turso_url
                        .replace('libsql://', 'https://')
                        .replace('sqlite+libsql://', 'https://'))
            conn = libsql.connect(
                database=_LOCAL_DB,
                sync_url=sync_url,
                auth_token=turso_token,
            )
            conn.sync()
            conn.close()
            print('✅ Turso pull sync complete (cold start)')
        except Exception as e:
            print(f'⚠️  Turso pull sync failed: {e}')

    threading.Thread(target=_do, daemon=True).start()


def _patch_dialect(engine):
    engine.dialect.get_isolation_level         = lambda conn: 'SERIALIZABLE'
    engine.dialect.get_default_isolation_level = lambda conn: 'SERIALIZABLE'


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY']                     = os.environ.get('SECRET_KEY', 'potato-corner-secret-2025')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['PERMANENT_SESSION_LIFETIME']      = timedelta(days=30)
    app.config['GOOGLE_CLIENT_ID']               = os.environ.get('GOOGLE_CLIENT_ID', '')
    app.config['GOOGLE_CLIENT_SECRET']           = os.environ.get('GOOGLE_CLIENT_SECRET', '')

    # ── Database: plain sqlite3 on local replica file ─────────────────────────
    # We always use sqlite3 directly — fast, no network, works on Vercel.
    # Turso sync happens in background threads after writes (turso_push_sync).
    use_turso = bool(os.environ.get('TURSO_DATABASE_URL') and os.environ.get('TURSO_AUTH_TOKEN'))

    # Use the local replica file for Turso, or a simple local db otherwise
    db_path = _LOCAL_DB if use_turso else '/tmp/potato_corner.db'

    def _creator():
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        return conn

    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite+pysqlite:///'
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'creator': _creator,
        'poolclass': __import__('sqlalchemy.pool', fromlist=['NullPool']).NullPool,
    }

    db.init_app(app)

    with app.app_context():
        _patch_dialect(db.engine)
        # Initialize schema immediately — sqlite3 is instant, no network
        try:
            db.metadata.create_all(bind=db.engine, checkfirst=True)
            from init_db import run_migrations
            run_migrations(db.engine)
            init_database()
            print('✅ DB schema ready')
        except Exception as e:
            print(f'❌ DB init error: {e}')
            import traceback; traceback.print_exc()

    # Pull latest from Turso in background after cold start
    if use_turso:
        turso_pull_sync()

    login_manager.init_app(app)
    login_manager.login_view = None
    login_manager.login_message = ''

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
        try:
            return User.query.get(int(user_id))
        except Exception:
            db.session.rollback()
            return None

    @app.before_request
    def before_each_request():
        try:
            db.session.expire_all()
        except Exception:
            pass

    register_routes(app)

    # ── Google OAuth ──────────────────────────────────────────────────────────
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

            if not user.is_active:
                flash('Your account has been disabled. Please contact support.', 'danger')
                return redirect(url_for('login'))
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


app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f'🚀 Starting Potato Corner at http://0.0.0.0:{port}')
    app.run(host='0.0.0.0', port=port, debug=False)
