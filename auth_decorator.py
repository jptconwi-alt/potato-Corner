from functools import wraps
from flask import redirect, url_for, flash, session, request, jsonify
from flask_login import current_user


def _is_ajax():
    """Return True if the request looks like a fetch/AJAX call."""
    ct = request.content_type or ''
    return (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in ct
        or 'multipart/form-data' in ct
        or 'application/x-www-form-urlencoded' in ct
    )


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            if _is_ajax():
                return jsonify({
                    'success': False,
                    'message': 'Session expired. Please log in again.',
                    'redirect': url_for('admin_login')
                }), 401
            return redirect(url_for('admin_login'))
        if not current_user.is_admin:
            if _is_ajax():
                return jsonify({
                    'success': False,
                    'message': 'Admin access required.'
                }), 403
            flash('This area requires an admin account.', 'danger')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated