from functools import wraps
from flask import redirect, url_for, flash, session
from flask_login import current_user

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            # Not logged in → admin login page (no flash, no noise)
            return redirect(url_for('admin_login'))
        if not current_user.is_admin:
            # Logged in as regular user → admin login with message
            flash('This area requires an admin account.', 'danger')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated
