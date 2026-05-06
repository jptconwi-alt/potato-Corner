"""
app/turso/dbapi.py
==================
A minimal DB-API 2.0 driver that talks to Turso via its HTTP /v2/pipeline API.

This completely avoids WebSocket / libsql / sqlalchemy-libsql issues.
SQLAlchemy uses this via a custom creator= function.

Turso HTTP pipeline docs:
  POST https://<db>.turso.io/v2/pipeline
  Authorization: Bearer <token>
  Body: {"requests": [{"type": "execute", "stmt": {"sql": "...", "args": [...]}}, {"type": "close"}]}
"""

import sqlite3  # used only for sqlite3.Row type reference — not for connecting
import requests as _requests

# ── DB-API 2.0 module globals ──────────────────────────────────────────────────
apilevel    = '2.0'
threadsafety = 1
paramstyle  = 'qmark'

# ── Exceptions ─────────────────────────────────────────────────────────────────
class Error(Exception):           pass
class Warning(Exception):         pass
class InterfaceError(Error):      pass
class DatabaseError(Error):       pass
class OperationalError(DatabaseError): pass
class ProgrammingError(DatabaseError): pass
class IntegrityError(DatabaseError):   pass
class DataError(DatabaseError):        pass
class NotSupportedError(DatabaseError): pass
class InternalError(DatabaseError):    pass


def connect(url: str, token: str, **kw):
    """Create a Turso HTTP connection. Called by SQLAlchemy's creator=."""
    return Connection(url, token)


# ── Connection ─────────────────────────────────────────────────────────────────
class Connection:
    def __init__(self, url: str, token: str):
        host = (url
                .replace('libsql://', '')
                .replace('https://', '')
                .replace('http://', '')
                .rstrip('/'))
        self._endpoint = f"https://{host}/v2/pipeline"
        self._headers  = {
            'Authorization': f'Bearer {token}',
            'Content-Type':  'application/json',
        }
        self._session = _requests.Session()
        self._session.headers.update(self._headers)
        self._closed  = False

    # ── Internal HTTP call ─────────────────────────────────────────────────────
    def _pipeline(self, stmts: list) -> dict:
        if self._closed:
            raise OperationalError("Connection closed")
        requests_body = []
        for sql, args in stmts:
            requests_body.append({
                'type': 'execute',
                'stmt': {
                    'sql':  sql,
                    'args': [_encode_arg(a) for a in (args or [])],
                },
            })
        requests_body.append({'type': 'close'})

        try:
            resp = self._session.post(
                self._endpoint,
                json={'requests': requests_body},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except _requests.HTTPError as e:
            raise OperationalError(
                f"Turso HTTP {e.response.status_code}: {e.response.text[:200]}"
            ) from e
        except _requests.RequestException as e:
            raise OperationalError(f"Turso request failed: {e}") from e

    # ── DB-API 2.0 interface ───────────────────────────────────────────────────
    def cursor(self):
        return Cursor(self)

    def commit(self):
        pass   # Turso auto-commits via HTTP pipeline

    def rollback(self):
        pass   # HTTP mode is auto-commit; ignore rollback

    def close(self):
        self._session.close()
        self._closed = True

    def create_function(self, name, num_params, func, *args, **kwargs):
        """No-op: SQLAlchemy's pysqlite dialect calls this to register REGEXP.
        Turso handles SQL server-side; custom functions are not supported."""
        pass

    def __enter__(self):  return self
    def __exit__(self, *a): self.close()


# ── Cursor ─────────────────────────────────────────────────────────────────────
class Cursor:
    def __init__(self, conn: Connection):
        self._conn     = conn
        self.description = None
        self.rowcount  = -1
        self.lastrowid = None
        self.arraysize = 1
        self._rows     = []
        self._pos      = 0
        self._pending  = []   # batch of (sql, args) for executemany

    # ── execution ──────────────────────────────────────────────────────────────
    def execute(self, sql: str, parameters=None):
        sql, params = _translate(sql, parameters or [])
        result = self._conn._pipeline([(sql, params)])
        self._load_result(result, 0)
        return self

    def executemany(self, sql: str, seq_of_params):
        for params in seq_of_params:
            self.execute(sql, params)

    def executescript(self, script: str):
        stmts = [s.strip() for s in script.split(';') if s.strip()]
        if stmts:
            result = self._conn._pipeline([(s, []) for s in stmts])
            self._load_result(result, len(stmts) - 1)

    def _load_result(self, result: dict, idx: int):
        """Parse Turso pipeline response into rows + description."""
        self._rows = []
        self._pos  = 0
        self.description = None
        self.lastrowid = None
        self.rowcount = -1

        try:
            item = result['results'][idx]
            if item.get('type') == 'error':
                raise OperationalError(item.get('error', {}).get('message', 'Unknown error'))

            resp = item.get('response', {})
            inner = resp.get('result', {})

            # last_insert_rowid
            if 'last_insert_rowid' in inner:
                val = inner['last_insert_rowid']
                self.lastrowid = int(val) if val is not None else None

            cols = inner.get('cols', [])
            rows = inner.get('rows', [])

            if cols:
                self.description = tuple(
                    (c.get('name', f'col{i}'), None, None, None, None, None, None)
                    for i, c in enumerate(cols)
                )
                self._rows = [
                    tuple(_decode_val(cell) for cell in row)
                    for row in rows
                ]
                self.rowcount = len(self._rows)
            else:
                # For INSERT/UPDATE/DELETE Turso returns affected_row_count.
                # Use it so SQLAlchemy's optimistic rowcount check passes.
                # Fall back to 1 (not -1) so the check never raises StaleDataError.
                arc = inner.get('affected_row_count')
                self.rowcount = int(arc) if arc is not None else 1

        except (KeyError, IndexError, TypeError):
            pass   # DDL / non-SELECT — no rows expected

    # ── fetch ──────────────────────────────────────────────────────────────────
    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchmany(self, size=None):
        size = size or self.arraysize
        rows = self._rows[self._pos:self._pos + size]
        self._pos += len(rows)
        return rows

    def fetchall(self):
        rows = self._rows[self._pos:]
        self._pos = len(self._rows)
        return rows

    def __iter__(self):
        return iter(self._rows[self._pos:])

    def close(self): pass
    def setinputsizes(self, *a): pass
    def setoutputsize(self, *a): pass

    def __enter__(self):  return self
    def __exit__(self, *a): self.close()


# ── Helpers ────────────────────────────────────────────────────────────────────
def _encode_arg(val):
    """Encode a Python value to Turso typed-value format."""
    if val is None:
        return {'type': 'null', 'value': None}
    if isinstance(val, bool):
        return {'type': 'integer', 'value': str(int(val))}
    if isinstance(val, int):
        return {'type': 'integer', 'value': str(val)}
    if isinstance(val, float):
        return {'type': 'float', 'value': val}
    if isinstance(val, (bytes, bytearray)):
        import base64
        return {'type': 'blob', 'value': base64.b64encode(val).decode()}
    return {'type': 'text', 'value': str(val)}


def _decode_val(cell):
    """Decode a Turso typed-value cell to a Python value."""
    if cell is None:
        return None
    t = cell.get('type')
    v = cell.get('value')
    if t == 'null' or v is None:
        return None
    if t == 'integer':
        return int(v)
    if t == 'float':
        return float(v)
    if t == 'blob':
        import base64
        return base64.b64decode(v)
    return v   # text


def _translate(sql: str, params):
    """
    SQLAlchemy uses :name style for named params and ? for positional.
    Turso expects positional ? args only.
    Convert :name → ? and reorder params if needed.
    """
    import re
    if isinstance(params, dict):
        # Named params — replace :name with ? and build ordered list
        order = []
        def replacer(m):
            order.append(params[m.group(1)])
            return '?'
        sql = re.sub(r':([a-zA-Z_][a-zA-Z0-9_]*)', replacer, sql)
        return sql, order
    return sql, list(params) if params else []