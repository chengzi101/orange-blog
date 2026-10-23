"""Persistent, revocable opaque sessions and bounded administrator authentication.

No legacy signed-cookie authentication is imported into this session store.
"""
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone

from flask import g, request, session
from flask.sessions import SecureCookieSession, SessionInterface

TOKEN = re.compile(r'[A-Za-z0-9_-]{43}\Z')


def init_auth_schema(db):
    statements = (
        '''CREATE TABLE IF NOT EXISTS auth_sessions (
           session_hash TEXT PRIMARY KEY, data TEXT NOT NULL,
           reader_id INTEGER, admin_stamp TEXT NOT NULL DEFAULT '',
           created_at INTEGER NOT NULL, accessed_at INTEGER NOT NULL,
           expires_at INTEGER NOT NULL, absolute_expires_at INTEGER NOT NULL,
           revoked_at INTEGER, revision INTEGER NOT NULL DEFAULT 0)''',
        'CREATE INDEX IF NOT EXISTS idx_auth_sessions_reader ON auth_sessions(reader_id,revoked_at)',
        'CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions(absolute_expires_at)',
        '''CREATE TABLE IF NOT EXISTS admin_login_tickets (
           token_hash TEXT PRIMARY KEY, credential_stamp TEXT NOT NULL,
           created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, consumed_at INTEGER)''',
        'CREATE INDEX IF NOT EXISTS idx_admin_tickets_expiry ON admin_login_tickets(expires_at)',
        '''CREATE TABLE IF NOT EXISTS auth_rate_limits (
           rate_key TEXT PRIMARY KEY, window_start INTEGER NOT NULL,
           attempts INTEGER NOT NULL CHECK(attempts>0))''',
        'CREATE INDEX IF NOT EXISTS idx_auth_rate_limits_window ON auth_rate_limits(window_start)',
    )
    for statement in statements:
        db.execute(statement)


def keyed_digest(secret, value):
    return hmac.new(str(secret).encode('utf-8'), value.encode('utf-8'), hashlib.sha256).hexdigest()


def auth_identity(data):
    return (bool(data.get('admin_authenticated')), data.get('reader_id'), data.get('reader_version'),
            data.get('csrf_token') if data.get('admin_authenticated') else None,
            data.get('reader_csrf') if data.get('reader_id') else None)


class StoredSession(SecureCookieSession):
    def __init__(self, data=None, *, sid=None, row=None):
        super().__init__(data)
        self.sid = sid or secrets.token_urlsafe(32)
        self.row = row
        self.initial_identity = auth_identity(data or {})


class SQLiteSessionInterface(SessionInterface):
    session_class = StoredSession

    def __init__(self, get_db, credentials):
        self.get_db = get_db
        self.credentials = credentials

    def database(self, app):
        db = self.get_db()
        if not app.extensions.get('auth_schema_ready'):
            with db:
                init_auth_schema(db)
            app.extensions['auth_schema_ready'] = True
        return db

    def admin_stamp(self, app):
        username, password_hash = self.credentials()
        return keyed_digest(app.secret_key, 'admin-credentials\0' + username + '\0' + password_hash)

    def session_hash(self, app, sid):
        # Secret rotation also makes all old identifiers unreachable.
        return keyed_digest(app.secret_key, 'session\0' + sid)

    def open_session(self, app, req):
        try:
            db = self.database(app)
            sid = req.cookies.get(self.get_cookie_name(app), '')
            if not TOKEN.fullmatch(sid):
                return self.session_class()
            row = db.execute('SELECT * FROM auth_sessions WHERE session_hash=?',
                             (self.session_hash(app, sid),)).fetchone()
            now = int(time.time())
            if not row or row['revoked_at'] is not None or min(row['expires_at'], row['absolute_expires_at']) <= now:
                return self.session_class()
            data = json.loads(row['data'])
            if not isinstance(data, dict):
                return self.session_class()
            valid = True
            if data.get('admin_authenticated'):
                valid = hmac.compare_digest(row['admin_stamp'], self.admin_stamp(app))
            uid = data.get('reader_id')
            if uid is not None:
                user = db.execute('SELECT disabled_at,session_version FROM reader_users WHERE id=?', (uid,)).fetchone()
                valid = valid and user is not None and not user['disabled_at'] and user['session_version'] == data.get('reader_version')
            if not valid:
                with db:
                    db.execute('UPDATE auth_sessions SET revoked_at=? WHERE session_hash=? AND revoked_at IS NULL',
                               (now, row['session_hash']))
                return self.session_class()
            return self.session_class(data, sid=sid, row=dict(row))
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            g.auth_storage_unavailable = True
            return self.session_class()
        except (ValueError, TypeError):
            # Corrupt stored JSON never becomes an authenticated session.
            return self.session_class()

    def _delete_cookie(self, app, response):
        response.delete_cookie(self.get_cookie_name(app), domain=self.get_cookie_domain(app),
                               path=self.get_cookie_path(app), secure=self.get_cookie_secure(app),
                               httponly=self.get_cookie_httponly(app), samesite=self.get_cookie_samesite(app))

    def save_session(self, app, current, response):
        if getattr(g, 'auth_storage_unavailable', False):
            return
        if current.accessed:
            response.vary.add('Cookie')
        now = int(time.time())
        row = current.row
        rotate = bool(row and auth_identity(current) != current.initial_identity)
        touched = bool(row and now - row['accessed_at'] >= 60)
        if not current and not row:
            return
        if current and row and not (current.modified or rotate or touched):
            return
        try:
            db = self.database(app)
            with db:
                db.execute('BEGIN IMMEDIATE')
                if not current:
                    if row:
                        db.execute('UPDATE auth_sessions SET revoked_at=? WHERE session_hash=? AND revoked_at IS NULL',
                                   (now, row['session_hash']))
                    self._delete_cookie(app, response)
                    return
                if rotate:
                    # Concurrent logout / expiry wins; harmless metadata writes
                    # on the same still-active session do not block a fresh login.
                    changed = db.execute('UPDATE auth_sessions SET revoked_at=? WHERE session_hash=? '
                                         'AND revoked_at IS NULL AND expires_at>? AND absolute_expires_at>?',
                                         (now, row['session_hash'], now, now)).rowcount
                    if not changed:
                        return
                    current.sid = secrets.token_urlsafe(32)
                created = now if not row or rotate else row['created_at']
                absolute = created + int(app.config['AUTH_ABSOLUTE_SECONDS']) if not row or rotate else row['absolute_expires_at']
                # Reader logins/logouts may rotate a shared admin session, but
                # cannot refresh the administrator's absolute authentication age.
                if row and current.initial_identity[0] and current.get('admin_authenticated') and not getattr(current, '_fresh_admin_auth', False):
                    created, absolute = row['created_at'], row['absolute_expires_at']
                idle = int(app.config['ADMIN_IDLE_SECONDS'] if current.get('admin_authenticated') else app.config['READER_IDLE_SECONDS'])
                expires = min(now + idle, absolute)
                stamp = self.admin_stamp(app) if current.get('admin_authenticated') else ''
                data = json.dumps(dict(current), ensure_ascii=False, separators=(',', ':'))
                if not row or rotate:
                    db.execute('INSERT INTO auth_sessions(session_hash,data,reader_id,admin_stamp,created_at,accessed_at,expires_at,absolute_expires_at) '
                               'VALUES(?,?,?,?,?,?,?,?)', (self.session_hash(app, current.sid), data, current.get('reader_id'), stamp,
                                                        created, now, expires, absolute))
                else:
                    changed = db.execute('UPDATE auth_sessions SET data=?,reader_id=?,admin_stamp=?,accessed_at=?,expires_at=?,revision=revision+1 '
                                         'WHERE session_hash=? AND revision=? AND revoked_at IS NULL AND expires_at>? AND absolute_expires_at>?',
                                         (data, current.get('reader_id'), stamp, now, expires, row['session_hash'], row['revision'], now, now)).rowcount
                    if not changed:
                        return
            response.set_cookie(self.get_cookie_name(app), current.sid,
                                expires=datetime.fromtimestamp(expires, timezone.utc),
                                httponly=self.get_cookie_httponly(app), secure=self.get_cookie_secure(app),
                                samesite=self.get_cookie_samesite(app), domain=self.get_cookie_domain(app),
                                path=self.get_cookie_path(app))
            response.vary.add('Cookie')
        except sqlite3.DatabaseError:
            # Do not claim that login or logout succeeded when persistence failed.
            response.status_code = 503
            response.set_data(json.dumps({'ok': False, 'message': '服务暂时繁忙，请稍后重试。'}, ensure_ascii=False))
            response.content_type = 'application/json; charset=utf-8'
            response.headers['Retry-After'] = '5'
            response.headers['Cache-Control'] = 'private, no-store'
            response.headers.pop('Set-Cookie', None)


def revoke_reader_sessions(db, user_id):
    db.execute('UPDATE auth_sessions SET revoked_at=? WHERE reader_id=? AND revoked_at IS NULL',
               (int(time.time()), user_id))


class LoginLimited(Exception):
    def __init__(self, retry_after, scope):
        self.retry_after, self.scope = retry_after, scope


def reserve_admin_login(app, db, ip, account):
    """All workers share three fixed-window budgets; no permanent account lock."""
    now = int(time.time())
    budgets = [('ip', ip, 5, 60), ('account', account, 20, 900), ('global', 'all', 100, 900)]
    with db:
        db.execute('BEGIN IMMEDIATE')
        writes = []
        for kind, value, limit, window in budgets:
            key = 'admin-login:' + kind + ':' + keyed_digest(app.secret_key, value)
            row = db.execute('SELECT * FROM auth_rate_limits WHERE rate_key=?', (key,)).fetchone()
            active = row is not None and row['window_start'] > now-window
            if active and row['attempts'] >= limit:
                raise LoginLimited(max(1, row['window_start'] + window - now), kind)
            writes.append((key, row['window_start'] if active else now, row['attempts']+1 if active else 1))
        db.executemany('INSERT INTO auth_rate_limits VALUES(?,?,?) ON CONFLICT(rate_key) DO UPDATE SET '
                       'window_start=excluded.window_start,attempts=excluded.attempts', writes)
        db.execute('DELETE FROM auth_rate_limits WHERE window_start<=?', (now-86400,))


def issue_admin_ticket(app, db):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO admin_login_tickets VALUES(?,?,?,?,NULL)',
                   (keyed_digest(app.secret_key, 'ticket\0'+token), app.session_interface.admin_stamp(app), now, now+60))
        db.execute('DELETE FROM admin_login_tickets WHERE expires_at<=?', (now-86400,))
    return token


def consume_admin_ticket(app, db, token):
    if not TOKEN.fullmatch(token):
        return False
    now = int(time.time())
    with db:
        db.execute('BEGIN IMMEDIATE')
        result = db.execute('UPDATE admin_login_tickets SET consumed_at=? WHERE token_hash=? '
                            'AND consumed_at IS NULL AND expires_at>? AND credential_stamp=?',
                            (now, keyed_digest(app.secret_key, 'ticket\0'+token), now, app.session_interface.admin_stamp(app)))
        return result.rowcount == 1
