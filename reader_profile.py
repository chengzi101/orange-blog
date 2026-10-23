"""Self-service reader profile; identity always comes from the validated session."""
import io
import math
import re
import secrets
import sqlite3
import unicodedata
from flask import Blueprint, abort, g, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


def init_profile(db):
    columns = {row[1] for row in db.execute('PRAGMA table_info(reader_users)')}
    if 'avatar_png' not in columns:
        db.execute('ALTER TABLE reader_users ADD COLUMN avatar_png BLOB')
    if 'avatar_key' not in columns:
        db.execute("ALTER TABLE reader_users ADD COLUMN avatar_key TEXT NOT NULL DEFAULT ''")
    if 'nickname_change_used' not in columns:
        db.execute('ALTER TABLE reader_users ADD COLUMN nickname_change_used INTEGER NOT NULL DEFAULT 0 CHECK(nickname_change_used IN (0,1))')


def avatar_url(user_id, key, is_owner=False):
    if key and re.fullmatch('[0-9a-f]{64}', key):
        return url_for('profile.avatar', user_id=user_id, key=key)
    return '/static/orange-avatar-20261017.png' if is_owner else None


def install_profile(app, get_db, now_iso, current_user, csrf, validate_token, throttle, error):
    bp = Blueprint('profile', __name__)
    notices = {'nickname':'昵称已保存，下次登录请使用新昵称。',
               'nickname_unchanged':'昵称未改变，未使用修改次数。',
               'password':'密码已修改，其他设备的旧登录已失效。'}

    def page(user, message='', code=200):
        db = get_db()
        account = db.execute('SELECT id,nickname,created_at,avatar_key,nickname_change_used FROM reader_users WHERE id=?', (user['id'],)).fetchone()
        total = db.execute('SELECT (SELECT count(*) FROM post_comments WHERE user_id=? AND deleted_at IS NULL)+(SELECT count(*) FROM project_comments WHERE user_id=? AND deleted_at IS NULL)', (user['id'],user['id'])).fetchone()[0]
        pages = max(1, math.ceil(total / 20))
        try:
            number = max(1, min(pages, int(request.args.get('page', 1))))
        except ValueError:
            number = 1
        comments = db.execute("SELECT * FROM (SELECT c.id,c.content,c.created_at,c.status,c.deleted_at,c.parent_id,'post' AS content_kind,"
                              "CASE WHEN p.status='published' THEN p.title ELSE '文章暂不可访问' END AS post_title,"
                              "CASE WHEN p.status='published' THEN p.slug ELSE NULL END AS slug "
                              'FROM post_comments c LEFT JOIN posts p ON p.id=c.post_id WHERE c.user_id=? AND c.deleted_at IS NULL '
                              "UNION ALL SELECT c.id,c.content,c.created_at,c.status,c.deleted_at,c.parent_id,'project' AS content_kind,"
                              "CASE WHEN p.is_visible=1 THEN p.title ELSE '项目暂不可访问' END AS post_title,"
                              "CASE WHEN p.is_visible=1 THEN p.slug ELSE NULL END AS slug "
                              'FROM project_comments c LEFT JOIN projects p ON p.id=c.project_id WHERE c.user_id=? AND c.deleted_at IS NULL '
                              ') ORDER BY created_at DESC,id DESC LIMIT 20 OFFSET ?', (user['id'],user['id'], (number-1)*20)).fetchall()
        return render_template('reader_profile.html', account=account, comments=comments, total=total,
                               page=number, pages=pages, error=message,
                               notice=notices.get(request.args.get('saved')) if not message else None), code

    @bp.route('/account', methods=['GET', 'POST'])
    def account():
        user = current_user()
        if not user:
            return redirect(url_for('reader.login', next='/account'), code=303 if request.method == 'POST' else 302)
        if request.method == 'GET':
            return page(user)
        db = get_db()
        try:
            validate_token()
            action = request.form.get('action')
            if action not in ('nickname', 'password'):
                raise error('不支持的操作。', 400)
            throttle('profile:'+str(user['id']), 40, 900)
            if action == 'password':
                throttle('profile-password:'+str(user['id']), 10, 900)
            nickname = unicodedata.normalize('NFKC', request.form.get('nickname', '')).strip()
            if action == 'nickname' and (not 1 <= len(nickname) <= 24 or any(unicodedata.category(c).startswith('C') for c in nickname)):
                raise error('昵称需为 1–24 个字符，不能包含控制字符。')
            old = request.form.get('old_password', '')
            new = request.form.get('new_password', '')
            if action == 'password':
                if not old or len(old) > 128:
                    raise error('请输入正确的旧密码。', 422)
                if not 8 <= len(new) <= 24 or new != request.form.get('confirm_password', ''):
                    raise error('新密码需为 8–24 个字符，且两次输入一致。')
                if new == old:
                    raise error('新密码不能与旧密码相同。')
            with db:
                db.execute('BEGIN IMMEDIATE')
                active = db.execute('SELECT password_hash,disabled_at,session_version,nickname,nickname_change_used FROM reader_users WHERE id=?', (user['id'],)).fetchone()
                if not active or active['disabled_at'] or active['session_version'] != session.get('reader_version', 0):
                    raise error('账号状态已变化，请重新登录。', 401)
                if action == 'nickname':
                    if active['nickname_change_used']:
                        raise error('昵称只能自行修改一次，如需再次修改，请联系管理员修改。', 403)
                    if nickname == active['nickname']:
                        action = 'nickname_unchanged'
                    else:
                        changed = db.execute('UPDATE reader_users SET nickname=?,nickname_key=?,nickname_change_used=1 WHERE id=? AND nickname_change_used=0', (nickname,nickname.casefold(),user['id']))
                        if changed.rowcount != 1:
                            raise error('昵称只能自行修改一次，如需再次修改，请联系管理员修改。', 403)
                elif action == 'password':
                    if not check_password_hash(active['password_hash'], old):
                        raise error('旧密码不正确，密码未修改。', 422)
                    db.execute('UPDATE reader_users SET password_hash=?,session_version=session_version+1 WHERE id=?',
                               (generate_password_hash(new, method='scrypt'),user['id']))
                    db.execute('DELETE FROM owner_login_tickets WHERE user_id=?', (user['id'],))
                    next_version = active['session_version'] + 1
            if action == 'password':
                session['reader_version'] = next_version
                session['reader_csrf'] = secrets.token_urlsafe(32)
            g.pop('reader_user', None)
            # PRG feedback must survive late responses from existing session-sync/static requests.
            # This allowlisted query value is display-only and never authorizes a write.
            return redirect(url_for('profile.account', saved=action), code=303)
        except error as exc:
            db.rollback()
            return page(user, exc.message, exc.status)
        except sqlite3.IntegrityError:
            db.rollback()
            return page(user, '这个昵称已被使用，请更换昵称。', 409)
        except sqlite3.OperationalError:
            db.rollback()
            app.logger.exception('Reader profile database operation failed')
            return page(user, '服务暂时繁忙，请稍后重试。', 503)

    @bp.get('/reader/avatar/<int:user_id>/<key>.png')
    def avatar(user_id, key):
        if not re.fullmatch('[0-9a-f]{64}', key):
            abort(404)
        row = get_db().execute('SELECT avatar_png FROM reader_users WHERE id=? AND avatar_key=?', (user_id,key)).fetchone()
        if not row or not row['avatar_png']:
            abort(404)
        response = send_file(io.BytesIO(row['avatar_png']), mimetype='image/png', download_name='avatar.png', max_age=0)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'public, max-age=0, must-revalidate'
        return response

    @app.after_request
    def private_profile(response):
        if request.path == '/account':
            response.headers['Cache-Control'] = 'private, no-store'
            response.vary.add('Cookie')
        return response

    app.register_blueprint(bp)
