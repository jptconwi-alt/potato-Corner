import warnings
from datetime import timedelta
warnings.filterwarnings('ignore', category=DeprecationWarning, module='authlib')

from flask import Flask, redirect, url_for, session, flash
from models import db
from views import register_routes
from init_db import init_database
from flask_login import LoginManager, login_user
from models import User
from authlib.integrations.flask_client import OAuth
import os
from dotenv import load_dotenv

load_dotenv()
login_manager = LoginManager()


class _LibSQLConnection:
    """Wrapper around libsql_experimental.Connection for Vercel + Turso.

    Three problems solved here:

    1. Immutable C-extension type: libsql_experimental.Connection attributes
       can't be set directly, so we can't monkey-patch sqlite3 compat methods
       onto it.  This wrapper is a plain Python object — attributes are free.

    2. sqlite3 compat stubs: Vercel's vendored pysqlite dialect calls
       create_function(), set_authorizer(), etc. on every new connection.
       We stub them as no-ops.

    3. Local-first sync: libsql_experimental works in a local-first mode.
       Each connection starts as an in-memory SQLite DB.  We must call
       sync() after connecting to PULL the remote Turso state locally, and
       after every commit() to PUSH local writes back to Turso.
    """

    __slots__ = ('_conn',)

    def __init__(self, conn):
        self._conn = conn

    def cursor(self):                          return self._conn.cursor()
    def execute(self, *a, **kw):               return self._conn.execute(*a, **kw)
    def executemany(self, *a, **kw):           return self._conn.executemany(*a, **kw)
    def executescript(self, *a, **kw):         return self._conn.executescript(*a, **kw)

    def commit(self):
        self._conn.commit()
        # Push local writes to remote Turso after every commit
        try:
            self._conn.sync()
        except Exception as e:
            print(f"⚠️  libsql sync after commit failed: {e}")

    def rollback(self):
        try:
            return self._conn.rollback()
        except Exception as e:
            print(f"⚠️  libsql rollback failed (stale stream): {e}")
    def close(self):                           return self._conn.close()
    def sync(self):                            return self._conn.sync()

    @property
    def in_transaction(self):                  return self._conn.in_transaction

    @property
    def isolation_level(self):                 return self._conn.isolation_level

    @isolation_level.setter
    def isolation_level(self, value):          pass   # libsql manages this itself

    # sqlite3-compat stubs that pysqlite's post-connect hooks try to call
    def create_function(self, *a, **kw):       pass
    def create_aggregate(self, *a, **kw):      pass
    def set_authorizer(self, *a, **kw):        pass
    def set_trace_callback(self, *a, **kw):    pass
    def set_progress_handler(self, *a, **kw):  pass


def _patch_dialect_for_libsql(engine):
    """Stop the pysqlite dialect from running PRAGMA read_uncommitted.

    During first_connect, SQLAlchemy calls get_default_isolation_level()
    which executes PRAGMA read_uncommitted.  On a libsql connection with a
    remote sync_url this triggers a WAL write that fails with
    'wal_insert_begin failed'.  Returning a fixed string skips the PRAGMA.
    """
    engine.dialect.get_isolation_level         = lambda conn: 'SERIALIZABLE'
    engine.dialect.get_default_isolation_level = lambda conn: 'SERIALIZABLE'


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY']                     = os.environ.get('SECRET_KEY', 'potato-corner-secret-2025')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['PERMANENT_SESSION_LIFETIME']      = timedelta(days=30)
    app.config['SESSION_COOKIE_SAMESITE']         = 'Lax'   # survive normal navigation/refresh
    app.config['REMEMBER_COOKIE_DURATION']        = timedelta(days=30)
    app.config['REMEMBER_COOKIE_SAMESITE']        = 'Lax'
    app.config['GOOGLE_CLIENT_ID']               = os.environ.get('GOOGLE_CLIENT_ID', '')
    app.config['GOOGLE_CLIENT_SECRET']           = os.environ.get('GOOGLE_CLIENT_SECRET', '')

    # ── Database ──────────────────────────────────────────────────────────────
    turso_url   = os.environ.get('TURSO_DATABASE_URL', '')
    turso_token = os.environ.get('TURSO_AUTH_TOKEN', '')

    use_libsql = bool(turso_url and turso_token)

    if use_libsql:
        try:
            import libsql_experimental as libsql
            import threading

            sync_url = (turso_url
                        .replace('libsql://', 'https://')
                        .replace('sqlite+libsql://', 'https://'))

            # Use /tmp for the local replica — writable on Vercel.
            # Each Vercel serverless instance gets its own /tmp, so each
            # instance maintains its own local replica. Writes are pushed
            # to Turso via sync() after every commit, reads pull fresh
            # data via sync() before each request.
            _local_db_path = '/tmp/libsql_replica.db'

            # Per-instance connection — created once per cold start.
            # This avoids exceeding Turso's connection limit (which happens
            # when using StaticPool with a singleton connection held open
            # across all concurrent requests on the same instance).
            _instance_conn = None
            _instance_lock = threading.Lock()

            def _get_instance_connection():
                nonlocal _instance_conn
                with _instance_lock:
                    if _instance_conn is None:
                        raw = libsql.connect(
                            database=_local_db_path,
                            sync_url=sync_url,
                            auth_token=turso_token,
                        )
                        # Initial sync on cold start
                        try:
                            raw.sync()
                            print("✅ libsql cold-start sync complete")
                        except Exception as e:
                            print(f"⚠️  libsql cold-start sync failed: {e}")
                        _instance_conn = raw
                    return _instance_conn

            def _creator():
                raw = _get_instance_connection()
                # Pull latest remote state before each request
                try:
                    raw.sync()
                except Exception as e:
                    print(f"⚠️  libsql pre-request sync failed: {e}")
                    # Connection may be stale — recreate it
                    nonlocal _instance_conn
                    with _instance_lock:
                        try:
                            _instance_conn = libsql.connect(
                                database=_local_db_path,
                                sync_url=sync_url,
                                auth_token=turso_token,
                            )
                            _instance_conn.sync()
                            raw = _instance_conn
                        except Exception as e2:
                            print(f"⚠️  libsql reconnect failed: {e2}")
                return _LibSQLConnection(raw)

            app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite+pysqlite://'
            app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
                'creator': _creator,
                # NullPool: don't pool connections — _creator() manages
                # the instance-level connection lifetime itself, and pooling
                # on top causes "stream not found" errors when Turso closes
                # idle streams.
                'poolclass': __import__('sqlalchemy.pool', fromlist=['NullPool']).NullPool,
            }

        except ImportError:
            use_libsql = False

    if not use_libsql:
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////tmp/potato_corner.db'

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = None   # We handle redirects manually in decorators
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
            # Column mismatch (e.g. is_active not yet migrated on this env)
            # Return None so Flask-Login marks session unauthenticated instead of 500
            db.session.rollback()
            return None

    with app.app_context():
        if use_libsql:
            _patch_dialect_for_libsql(db.engine)
        try:
            db.metadata.create_all(bind=db.engine, checkfirst=True)
            from init_db import run_migrations
            run_migrations(db.engine)
            init_database()
        except Exception as e:
            print(f"❌ DB init error: {e}")
            import traceback; traceback.print_exc()

    @app.before_request
    def expire_session_on_request():
        """Expire all ORM-cached objects at the start of every request.

        libsql works in local-first mode: _creator() calls sync() before each
        request to pull the latest remote state into the local replica.  But
        SQLAlchemy's identity-map (session cache) still holds Python objects
        from a previous request.  Those cached objects shadow the freshly-
        synced DB rows, causing the 'refresh toggle' bug where cart items
        reappear or orders disappear on the first refresh after checkout.

        expire_all() invalidates every cached ORM object so the *next* attribute
        access for each object issues a fresh SELECT against the already-synced
        local replica.  This is cheap (no SQL yet) and fixes the stale-read bug.
        """
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
            login_user(user, remember=True)
            session.permanent = True   # keep session alive across browser restarts
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


# Create the global app instance for Gunicorn / Vercel
app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"🚀 Starting Potato Corner at http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)