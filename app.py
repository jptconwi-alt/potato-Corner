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
    """Wrapper around libsql_experimental.Connection.

    Vercel's vendored SQLAlchemy uses the pysqlite dialect, which calls
    sqlite3-specific methods on every new connection (create_function for
    REGEXP, set_authorizer, etc.).  libsql_experimental's Connection is a
    C-extension type with immutable attributes, so we wrap it in a plain
    Python object that forwards real DB calls and stubs out the sqlite3
    compat methods.
    """

    __slots__ = ('_conn',)

    def __init__(self, conn):
        self._conn = conn

    def cursor(self):                          return self._conn.cursor()
    def execute(self, *a, **kw):               return self._conn.execute(*a, **kw)
    def executemany(self, *a, **kw):           return self._conn.executemany(*a, **kw)
    def executescript(self, *a, **kw):         return self._conn.executescript(*a, **kw)
    def commit(self):                          return self._conn.commit()
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
    def create_function(self, *a, **kw):       pass   # pysqlite registers REGEXP here
    def create_aggregate(self, *a, **kw):      pass
    def set_authorizer(self, *a, **kw):        pass
    def set_trace_callback(self, *a, **kw):    pass
    def set_progress_handler(self, *a, **kw):  pass


def _patch_dialect_for_libsql(engine):
    """Override the pysqlite dialect's isolation-level detection.

    During first_connect the vendored SQLAlchemy runs PRAGMA read_uncommitted
    via dialect.initialize() → get_default_isolation_level().  On a libsql
    connection backed by a remote Turso database this PRAGMA triggers a WAL
    write that fails with 'wal_insert_begin failed'.  Returning a fixed value
    short-circuits the PRAGMA call entirely.
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
                return _LibSQLConnection(raw)

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
        # Patch the dialect INSIDE the app context — db.engine requires it.
        if use_libsql:
            _patch_dialect_for_libsql(db.engine)
        try:
            db.create_all()
            from init_db import run_migrations
            run_migrations(db.engine)
            init_database()
        except Exception as e:
            print(f"❌ DB error: {e}")
            import traceback; traceback.print_exc()

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