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

    def rollback(self):                        return self._conn.rollback()
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
    app.config['GOOGLE_CLIENT_ID']               = os.environ.get('GOOGLE_CLIENT_ID', '')
    app.config['GOOGLE_CLIENT_SECRET']           = os.environ.get('GOOGLE_CLIENT_SECRET', '')

    # ── Database ──────────────────────────────────────────────────────────────
    turso_url   = os.environ.get('TURSO_DATABASE_URL', '')
    turso_token = os.environ.get('TURSO_AUTH_TOKEN', '')

    use_libsql = bool(turso_url and turso_token)

    if use_libsql:
        try:
            import libsql_experimental as libsql

            sync_url = (turso_url
                        .replace('libsql://', 'https://')
                        .replace('sqlite+libsql://', 'https://'))

            def _creator():
                raw = libsql.connect(
                    database=':memory:',
                    sync_url=sync_url,
                    auth_token=turso_token,
                )
                # Pull the current remote state into local memory before
                # handing this connection to SQLAlchemy.  Without this, the
                # local :memory: DB is empty and every query gets
                # "no such table: products".
                try:
                    raw.sync()
                except Exception as e:
                    print(f"⚠️  libsql initial sync failed: {e}")
                return _LibSQLConnection(raw)

            # 'sqlite+pysqlite://' selects the SQLite dialect for SQL generation.
            # All actual connections come from _creator() above.
            app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite+pysqlite://'
            app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'creator': _creator}

        except ImportError:
            use_libsql = False

    if not use_libsql:
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////tmp/potato_corner.db'

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
        # db.engine requires an active app context — patch here, not before.
        if use_libsql:
            _patch_dialect_for_libsql(db.engine)
        db.create_all()
        from init_db import run_migrations
        run_migrations(db.engine)
        init_database()

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