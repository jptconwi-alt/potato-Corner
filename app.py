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

    Holds a reference to the global reconnect function so it can
    self-heal whenever the Turso Hrana stream expires (stream not found).
    """

    __slots__ = ('_conn', '_reconnect')

    # Sentinel strings that indicate the remote stream has been closed
    _STREAM_ERRORS = ('stream not found', 'hrana', 'status=404', 'stream expired')

    def __init__(self, conn, reconnect_fn=None):
        self._conn = conn
        self._reconnect = reconnect_fn  # callable() → new raw libsql conn

    def _is_stream_error(self, exc):
        msg = str(exc).lower()
        return any(k in msg for k in self._STREAM_ERRORS)

    def _heal(self):
        """Replace the dead raw connection with a fresh one."""
        if self._reconnect:
            try:
                self._conn = self._reconnect()
                print("✅ libsql stream healed — new connection established")
            except Exception as e:
                print(f"⚠️  libsql heal failed: {e}")

    def cursor(self):
        return self._conn.cursor()

    def execute(self, *a, **kw):
        try:
            return self._conn.execute(*a, **kw)
        except Exception as e:
            if self._is_stream_error(e):
                self._heal()
                return self._conn.execute(*a, **kw)
            raise

    def executemany(self, *a, **kw):
        return self._conn.executemany(*a, **kw)

    def executescript(self, *a, **kw):
        return self._conn.executescript(*a, **kw)

    def commit(self):
        try:
            self._conn.commit()
        except Exception as e:
            if self._is_stream_error(e):
                self._heal()
            else:
                raise
        try:
            self._conn.sync()
        except Exception as e:
            if not self._is_stream_error(e):
                print(f"⚠️  libsql sync after commit failed: {e}")

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception as e:
            if self._is_stream_error(e):
                # Stream is dead — heal silently; nothing to roll back on a dead stream
                self._heal()
            else:
                print(f"⚠️  libsql rollback failed: {e}")

    def close(self):
        pass  # singleton — never close; stream errors are handled by _heal()

    def sync(self):
        try:
            return self._conn.sync()
        except Exception as e:
            if self._is_stream_error(e):
                self._heal()
                return self._conn.sync()
            raise

    @property
    def in_transaction(self):
        try:
            return self._conn.in_transaction
        except Exception:
            return False

    @property
    def isolation_level(self):
        return self._conn.isolation_level

    @isolation_level.setter
    def isolation_level(self, value):
        pass

    def create_function(self, *a, **kw):       pass
    def create_aggregate(self, *a, **kw):      pass
    def set_authorizer(self, *a, **kw):        pass
    def set_trace_callback(self, *a, **kw):    pass
    def set_progress_handler(self, *a, **kw):  pass


def _patch_dialect_for_libsql(engine):
    engine.dialect.get_isolation_level         = lambda conn: 'SERIALIZABLE'
    engine.dialect.get_default_isolation_level = lambda conn: 'SERIALIZABLE'


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY']                     = os.environ.get('SECRET_KEY', 'potato-corner-secret-2025')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['PERMANENT_SESSION_LIFETIME']      = timedelta(days=30)
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

            _local_db_path = '/tmp/libsql_replica.db'
            _instance_conn = None
            _instance_lock = threading.Lock()

            def _make_raw_conn():
                """Create a fresh libsql connection and sync it (15 s timeout)."""
                import concurrent.futures
                def _connect():
                    c = libsql.connect(
                        database=_local_db_path,
                        sync_url=sync_url,
                        auth_token=turso_token,
                    )
                    c.sync()
                    return c
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(_connect)
                    return future.result(timeout=15)

            def _reconnect():
                """Replace the singleton with a brand-new connection.
                Called by _LibSQLConnection._heal() when stream not found."""
                nonlocal _instance_conn
                with _instance_lock:
                    try:
                        _instance_conn = _make_raw_conn()
                        print("✅ libsql reconnected — fresh stream")
                        return _instance_conn
                    except Exception as e:
                        print(f"⚠️  libsql reconnect failed: {e}")
                        raise

            def _get_instance_connection():
                nonlocal _instance_conn
                with _instance_lock:
                    if _instance_conn is None:
                        try:
                            _instance_conn = _make_raw_conn()
                            print("✅ libsql cold-start sync complete")
                        except Exception as e:
                            print(f"⚠️  libsql cold-start sync failed: {e}")
                            raise
                    return _instance_conn

            def _creator():
                """Called by SQLAlchemy NullPool for every new connection request."""
                raw = _get_instance_connection()
                # Pass _reconnect so the wrapper can self-heal on stream errors
                return _LibSQLConnection(raw, reconnect_fn=_reconnect)

            app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite+pysqlite://'
            app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
                'creator': _creator,
                'poolclass': __import__('sqlalchemy.pool', fromlist=['NullPool']).NullPool,
            }

        except ImportError:
            use_libsql = False

    if not use_libsql:
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////tmp/potato_corner.db'

    db.init_app(app)
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
        # Expire stale ORM cache so every request re-reads from the local replica.
        # We do NOT call raw.sync() here — that makes a blocking network call to
        # Turso on every request and causes 504 timeouts when the stream is slow.
        # Post-write syncs in _turso_sync() are enough to keep the replica fresh.
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


# Create the global app instance for Gunicorn / Vercel
app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"🚀 Starting Potato Corner at http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
