"""Explicit owner binding and single-use front-end login handoff."""
import hashlib
import hmac
import secrets
import time
import unicodedata
from functools import wraps

from flask import abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from security_helpers import constant_time_text_equal


def owner_id(db):
    row = db.execute("SELECT value FROM settings WHERE key='owner_reader_id'").fetchone()
    try:
        return int(row['value']) if row else None
    except (ValueError, TypeError):
        return None


def install_owner(app, get_db, now_iso):
    def admin(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get('admin_authenticated'):
                return redirect(url_for('admin_login', next='/admin/owner'))
            if request.method == 'POST':
                expected = session.get('csrf_token', '')
                if not expected or not constant_time_text_equal(expected, request.form.get('csrf_token', '')):
                    abort(400, '表单已失效，请刷新重试。')
            return view(*args, **kwargs)
        return wrapped

    @app.route('/admin/owner', methods=['GET', 'POST'])
    @admin
    def admin_owner():
        db = get_db()
        error = ''
        if request.method == 'POST':
            nickname = unicodedata.normalize('NFKC', request.form.get('nickname', '')).strip()
            password = request.form.get('password', '')
            action = request.form.get('action')
            if not 1 <= len(nickname) <= 24 or any(unicodedata.category(c).startswith('C') for c in nickname):
                error = '昵称需为 1–24 个字符，不能包含控制字符。'
            elif action not in ('create', 'bind'):
                abort(400)
            elif action == 'create' and password and not 8 <= len(password) <= 24:
                error = '如需设置密码，长度需为 8–24 个字符。'
            else:
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    user = db.execute('SELECT * FROM reader_users WHERE nickname_key=?', (nickname.casefold(),)).fetchone()
                    if action == 'bind':
                        if len(password) > 128 or not user or user['disabled_at'] or not check_password_hash(user['password_hash'], password):
                            error = '账号或密码不正确，无法绑定。'
                    elif user:
                        error = '昵称已被使用。如账号属于你，请选择绑定已有账号并验证密码。'
                    else:
                        uid = db.execute('INSERT INTO reader_users(nickname,nickname_key,password_hash,created_at) VALUES(?,?,?,?)',
                                         (nickname, nickname.casefold(), generate_password_hash(password or secrets.token_urlsafe(48), method='scrypt'), now_iso())).lastrowid
                        user = {'id': uid}
                    if not error:
                        db.execute("INSERT INTO settings(key,value) VALUES('owner_reader_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(user['id']),))
                        db.execute('DELETE FROM owner_login_tickets')
                if not error:
                    flash('博主账号已绑定，可以快捷登录前台。', 'success')
                    return redirect(url_for('admin_owner'), code=303)
        user = db.execute('SELECT id,nickname FROM reader_users WHERE id=?', (owner_id(db),)).fetchone()
        return render_template('admin_owner.html', owner=user, error=error), 422 if error else 200

    @app.post('/admin/owner/login')
    @admin
    def admin_owner_login():
        db = get_db()
        uid = owner_id(db)
        if not db.execute('SELECT 1 FROM reader_users WHERE id=? AND disabled_at IS NULL', (uid,)).fetchone():
            return redirect(url_for('admin_owner'), code=303)
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with db:
            db.execute('DELETE FROM owner_login_tickets WHERE expires_at<=?', (now,))
            db.execute('INSERT INTO owner_login_tickets VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), uid, now + 60))
        response = app.make_response(render_template('owner_handoff.html', token=token))
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.post('/auth/owner/consume')
    def owner_consume():
        # This POST uses a short-lived bearer ticket instead of an existing session.
        token = request.form.get('ticket', '')
        if not 32 <= len(token) <= 128:
            abort(400, '登录凭证无效，请从后台重新进入。')
        db = get_db()
        digest = hashlib.sha256(token.encode()).hexdigest()
        with db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM owner_login_tickets WHERE token_hash=?', (digest,)).fetchone()
            valid = row and row['expires_at'] > int(time.time()) and row['user_id'] == owner_id(db)
            user = db.execute('SELECT session_version FROM reader_users WHERE id=? AND disabled_at IS NULL', (row['user_id'],)).fetchone() if valid else None
            valid = valid and user is not None
            if row:
                db.execute('DELETE FROM owner_login_tickets WHERE token_hash=?', (digest,))
        if not valid:
            abort(400, '登录凭证已使用或过期，请从后台重新进入。')
        session['reader_id'] = row['user_id']
        session['reader_version'] = user['session_version']
        session['reader_csrf'] = secrets.token_urlsafe(32)
        session.permanent = True
        response = redirect('/articles', code=303)
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response
