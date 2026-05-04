"""
extensions.py — shared Flask extension instances.

Import from here instead of app.py to avoid circular imports.
"""
from flask_socketio import SocketIO

socketio = SocketIO()
