"""Administrator-only reader account management."""
import hmac
import math
import sqlite3
import unicodedata
from functools import wraps
from flask import abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash
from owner import owner_id
from security_helpers import constant_time_text_equal
from auth_security import revoke_reader_sessions


def install_accounts(app, get_db, now_iso):
    def admin(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get('admin_authenticated'):
                return redirect(url_for('admin_login', next=request.path))
            if request.method == 'POST':
                expected = session.get('csrf_token', '')
                if not expected or not constant_time_text_equal(expected, request.form.get('csrf_token', '')):
                    abort(400, '表单已失效，请刷新重试。')
            return view(*args, **kwargs)
        return wrapped

    @app.after_request
    def private_accounts(response):
        if request.path.startswith('/admin/accounts'):
            response.headers['Cache-Control'] = 'private, no-store'
            response.vary.add('Cookie')
        return response

    @app.get('/admin/accounts')
    @admin
    def admin_accounts():
        db = get_db()
        query = request.args.get('q', '').strip()[:100]
        status = request.args.get('status', '')
        where, params = [], []
        if query:
            where.append('(instr(lower(u.nickname),lower(?))>0 OR CAST(u.id AS TEXT)=?)')
            params.extend((query, query))
        if status in ('active', 'disabled'):
            where.append('u.disabled_at IS ' + ('NOT NULL' if status == 'disabled' else 'NULL'))
        sql = ' FROM reader_users u' + (' WHERE ' + ' AND '.join(where) if where else '')
        total = db.execute('SELECT COUNT(*)' + sql, params).fetchone()[0]
        pages = max(1, math.ceil(total / 20))
        try:
            page = max(1, min(pages, int(request.args.get('page', '1'))))
        except ValueError:
            page = 1
        users = db.execute('SELECT u.id,u.nickname,u.created_at,u.disabled_at,u.custom_title,'
                           '((SELECT COUNT(*) FROM post_comments c WHERE c.user_id=u.id AND c.deleted_at IS NULL)+(SELECT COUNT(*) FROM project_comments c WHERE c.user_id=u.id AND c.deleted_at IS NULL)) AS comments,'
                           '((SELECT COUNT(*) FROM post_likes l WHERE l.user_id=u.id)+(SELECT COUNT(*) FROM project_likes l WHERE l.user_id=u.id)) AS likes' + sql +
                           ' ORDER BY u.id DESC LIMIT 20 OFFSET ?', (*params, (page - 1) * 20)).fetchall()
        stats = db.execute('SELECT COUNT(*) AS total,COUNT(disabled_at) AS disabled FROM reader_users').fetchone()
        return render_template('admin_accounts.html', users=users, owner_id=owner_id(db), query=query,
                               status=status, total=total, page=page, pages=pages, stats=stats)

    @app.route('/admin/accounts/<int:user_id>', methods=['GET', 'POST'])
    @admin
    def admin_account(user_id):
        db = get_db()
        error, code = '', 200
        user = db.execute('SELECT id,nickname,created_at,disabled_at,custom_title FROM reader_users WHERE id=?', (user_id,)).fetchone()
        if not user:
            abort(404)
        if request.method == 'POST':
            action = request.form.get('action')
            if action not in ('rename', 'password', 'disable', 'enable', 'logout', 'title'):
                abort(400)
            nickname = unicodedata.normalize('NFKC', request.form.get('nickname', '')).strip()
            password = request.form.get('password', '')
            title = unicodedata.normalize('NFKC', request.form.get('custom_title', '')).strip()
            if action == 'title' and (len(title) > 12 or any(unicodedata.category(c).startswith('C') for c in title)):
                error = '头衔最多 12 个字符，不能包含控制字符；留空可清除。'
            if action == 'title' and title == '博主':
                error = '“博主”为系统头衔，请填写其他头衔。'
            if action == 'rename' and (not 1 <= len(nickname) <= 24 or any(unicodedata.category(c).startswith('C') for c in nickname)):
                error = '昵称需为 1–24 个字符，不能包含控制字符。'
            if action == 'password' and (not 8 <= len(password) <= 24 or password != request.form.get('confirm_password', '')):
                error = '密码需为 8–24 个字符，且两次输入一致。'
            if not error:
                try:
                    with db:
                        db.execute('BEGIN IMMEDIATE')
                        if action == 'title' and user_id == owner_id(db):
                            error = '博主账号使用系统头衔，无需设置自定义头衔。'
                        elif action == 'disable' and user_id == owner_id(db):
                            error = '当前绑定的博主账号不能停用，请先在“博主账号”更换绑定。'
                        else:
                            detail = ''
                            if action == 'title':
                                current = db.execute('SELECT custom_title FROM reader_users WHERE id=?', (user_id,)).fetchone()
                                db.execute('UPDATE reader_users SET custom_title=? WHERE id=?', (title, user_id))
                                detail = '原头衔：' + current['custom_title'] + '；新头衔：' + title
                            elif action == 'rename':
                                db.execute('UPDATE reader_users SET nickname=?,nickname_key=? WHERE id=?', (nickname, nickname.casefold(), user_id))
                                detail = '原昵称：' + user['nickname']
                            elif action == 'password':
                                db.execute('UPDATE reader_users SET password_hash=?,session_version=session_version+1 WHERE id=?', (generate_password_hash(password, method='scrypt'), user_id))
                            elif action in ('disable', 'enable'):
                                db.execute('UPDATE reader_users SET disabled_at=?,session_version=session_version+1 WHERE id=?', (now_iso() if action == 'disable' else None, user_id))
                            else:
                                db.execute('UPDATE reader_users SET session_version=session_version+1 WHERE id=?', (user_id,))
                            if action not in ('rename', 'title'):
                                db.execute('DELETE FROM owner_login_tickets WHERE user_id=?', (user_id,))
                                revoke_reader_sessions(db, user_id)
                            labels = {'title': '设置读者头衔', 'rename': '修改读者昵称', 'password': '重置读者密码', 'disable': '停用读者账号', 'enable': '恢复读者账号', 'logout': '退出读者所有会话'}
                            db.execute('INSERT INTO activity_log(action,item_type,item_title,detail,created_at) VALUES(?,?,?,?,?)',
                                       (labels[action], 'reader', 'ID ' + str(user_id), detail, now_iso()))
                            db.execute('DELETE FROM activity_log WHERE id NOT IN (SELECT id FROM activity_log ORDER BY id DESC LIMIT 300)')
                    if not error:
                        flash('账户操作已保存。' + ('该账号的已有登录已失效。' if action not in ('rename', 'title') else ''), 'success')
                        return redirect(url_for('admin_account', user_id=user_id), code=303)
                except sqlite3.IntegrityError:
                    error, code = '这个昵称已被使用，请更换昵称。', 409
            if error and code == 200:
                code = 422
        return render_template('admin_account.html', user=user, is_owner=user_id == owner_id(db), error=error), code
