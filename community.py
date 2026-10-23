"""Reader accounts and persistent article interactions for the existing Flask CMS."""
from pathlib import Path
from owner import owner_id, install_owner
from reader_profile import init_profile, install_profile, avatar_url
import hashlib
import hmac
import math
import re
import secrets
import sqlite3
import time
import unicodedata
from urllib.parse import urlsplit

from flask import Blueprint, g, request, session, render_template, redirect, url_for, flash
from moderation import init_moderation, install_moderation, reserve_submission, classify, client_ip, fingerprint, comment_pending
from werkzeug.security import generate_password_hash, check_password_hash
from security_helpers import constant_time_text_equal
from auth_security import LoginLimited
from public_limits import reserve_public, check_public
from query_indexes import init_query_indexes

SCHEMA = Path(__file__).with_name('migrations') / '20261019_reader_community.sql'
PAGE_SIZE = 20
VIEW_WINDOW = 30 * 60
_DUMMY_HASH = generate_password_hash('unavailable-reader-account', method='scrypt')


def init_community(db):
    # execute() retains the caller's transaction, unlike executescript().
    for statement in SCHEMA.read_text(encoding='utf-8').split(';'):
        if statement.strip():
            db.execute(statement)
    columns = {r[1] for r in db.execute('PRAGMA table_info(post_comments)')}
    for name in ('parent_id', 'reply_to_id'):
        if name not in columns:
            db.execute(f'ALTER TABLE post_comments ADD COLUMN {name} INTEGER REFERENCES post_comments(id) ON DELETE SET NULL')
    db.execute('CREATE INDEX IF NOT EXISTS idx_comments_parent ON post_comments(post_id,parent_id,created_at,id)')
    if 'deleted_at' not in columns:
        db.execute('ALTER TABLE post_comments ADD COLUMN deleted_at TEXT')
    user_columns = {r[1] for r in db.execute('PRAGMA table_info(reader_users)')}
    if 'disabled_at' not in user_columns:
        db.execute('ALTER TABLE reader_users ADD COLUMN disabled_at TEXT')
    if 'custom_title' not in user_columns:
        db.execute("ALTER TABLE reader_users ADD COLUMN custom_title TEXT NOT NULL DEFAULT ''")
    if 'session_version' not in user_columns:
        db.execute('ALTER TABLE reader_users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0')

    if 'floor' not in columns:
        db.execute('ALTER TABLE post_comments ADD COLUMN floor INTEGER')
    for row in db.execute('SELECT id,post_id FROM post_comments WHERE floor IS NULL ORDER BY created_at,id').fetchall():
        db.execute('UPDATE post_comments SET floor=(SELECT COALESCE(MAX(floor),0)+1 FROM post_comments WHERE post_id=?) WHERE id=?', (row['post_id'], row['id']))
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_comment_floor ON post_comments(post_id,floor)')
    db.execute('CREATE TABLE IF NOT EXISTS comment_likes (comment_id INTEGER NOT NULL REFERENCES post_comments(id) ON DELETE CASCADE,user_id INTEGER NOT NULL REFERENCES reader_users(id) ON DELETE CASCADE,created_at TEXT NOT NULL,PRIMARY KEY(comment_id,user_id))')
    db.execute('CREATE TABLE IF NOT EXISTS project_view_counts (project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE, views INTEGER NOT NULL DEFAULT 0)')
    db.execute('CREATE TABLE IF NOT EXISTS project_view_visitors (project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, visitor_key TEXT NOT NULL, last_counted_at INTEGER NOT NULL, PRIMARY KEY(project_id,visitor_key))')
    init_moderation(db)
    init_profile(db)
    init_query_indexes(db)
    from project_community import init_project_community
    init_project_community(db)


def snapshot_post_community(db, post_id):
    result = {table: [dict(row) for row in db.execute(
        f'SELECT * FROM {table} WHERE post_id=?', (post_id,))]
        for table in ('post_likes', 'post_comments', 'post_view_visitors')}
    result['comment_likes'] = [dict(row) for row in db.execute('SELECT l.* FROM comment_likes l JOIN post_comments c ON c.id=l.comment_id WHERE c.post_id=?', (post_id,))]
    return result


def restore_post_community(db, post_id, payload):
    fields = {
        'post_likes': ('user_id', 'created_at'),
        'post_comments': ('user_id', 'content', 'created_at', 'submission_id', 'deleted_at', 'status', 'reviewed_at', 'reviewed_by', 'moderation_reason', 'manual_approved', 'content_fingerprint', 'floor'),
        'post_view_visitors': ('visitor_key', 'last_counted_at'),
    }
    for table, columns in fields.items():
        comment_ids = {}
        for row in payload.get(table, []):
            if 'user_id' in columns and not db.execute(
                    'SELECT 1 FROM reader_users WHERE id=?', (row.get('user_id'),)).fetchone():
                continue
            names = ('post_id',) + columns
            inserted = db.execute(f'INSERT OR IGNORE INTO {table} ({",".join(names)}) '
                       f'VALUES ({",".join("?" for _ in names)})',
                       (post_id, *(row.get(key, {'status': 'pending', 'moderation_reason': '', 'manual_approved': 0, 'content_fingerprint': fingerprint(row['content'])}.get(key)) if key in ('deleted_at','status','reviewed_at','reviewed_by','moderation_reason','manual_approved','content_fingerprint','floor') else row[key] for key in columns)))
            if table == 'post_comments' and 'id' in row:
                restored = db.execute('SELECT id FROM post_comments WHERE user_id=? AND submission_id=?',
                                      (row['user_id'], row['submission_id'])).fetchone()
                if restored:
                    comment_ids[row['id']] = restored['id']
        if table == 'post_comments':
            for row in payload.get(table, []):
                if row.get('id') in comment_ids:
                    db.execute('UPDATE post_comments SET parent_id=?,reply_to_id=? WHERE id=?',
                               (comment_ids.get(row.get('parent_id')), comment_ids.get(row.get('reply_to_id')), comment_ids[row['id']]))
            for vote in payload.get('comment_likes', []):
                if vote['comment_id'] in comment_ids and db.execute('SELECT 1 FROM reader_users WHERE id=?', (vote['user_id'],)).fetchone():
                    db.execute('INSERT OR IGNORE INTO comment_likes VALUES (?,?,?)', (comment_ids[vote['comment_id']], vote['user_id'], vote['created_at']))
            for row in db.execute('SELECT id FROM post_comments WHERE post_id=? AND floor IS NULL ORDER BY created_at,id', (post_id,)).fetchall():
                db.execute('UPDATE post_comments SET floor=(SELECT COALESCE(MAX(floor),0)+1 FROM post_comments WHERE post_id=?) WHERE id=?', (post_id, row['id']))


def safe_return(value):
    value = value or '/'
    if not value.startswith('/') or value.startswith('//') or '\\' in value:
        return '/'
    if any(ord(char) < 32 for char in value):
        return '/'
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or parts.path.startswith(('/auth', '/api', '/admin')):
        return '/'
    return value


class ReaderError(Exception):
    def __init__(self, message, status=422, retry_after=None):
        self.message, self.status = message, status
        self.retry_after = retry_after


def install_community(app, get_db, now_iso, visitor_fingerprint, classify_client):
    bp = Blueprint('reader', __name__)

    def current_user():
        if 'reader_user' not in g:
            user_id = session.get('reader_id')
            row = get_db().execute('SELECT id,nickname,disabled_at,session_version,avatar_key FROM reader_users WHERE id=?', (user_id,)).fetchone() if isinstance(user_id, int) else None
            g.reader_user = {'id': row['id'], 'nickname': row['nickname'], 'avatar_url': avatar_url(row['id'], row['avatar_key'], row['id'] == owner_id(get_db()))} if row and not row['disabled_at'] and row['session_version'] == session.get('reader_version', 0) else None
            if not g.reader_user:
                session.pop('reader_id', None)
                session.pop('reader_version', None)
        return g.reader_user

    def csrf():
        if not session.get('reader_csrf'):
            session['reader_csrf'] = secrets.token_urlsafe(32)
        return session['reader_csrf']

    def validate_token():
        expected = session.get('reader_csrf', '')
        received = request.form.get('csrf_token', '') or request.headers.get('X-CSRF-Token', '')
        if not expected or not constant_time_text_equal(expected, received):
            raise ReaderError('页面已过期，请刷新后重试。', 400)

    def require_user():
        user = current_user()
        if not user:
            raise ReaderError('请先登录，再进行此操作。', 401)
        return user

    def published_post(post_id):
        post = get_db().execute("SELECT id,slug FROM posts WHERE id=? AND status='published'", (post_id,)).fetchone()
        if not post:
            raise ReaderError('文章不存在或尚未发布。', 404)
        return post

    def throttle(kind, limit, window):
        # Trust only the reverse proxy's overwritten X-Real-IP, as existing analytics do.
        client = client_ip()
        digest = hmac.new(str(app.secret_key).encode(), client.encode(), hashlib.sha256).hexdigest()
        key, now = kind + ':' + digest, int(time.time())
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM reader_rate_limits WHERE rate_key=?', (key,)).fetchone()
            if row and row['window_start'] > now - window and row['attempts'] >= limit:
                raise ReaderError('操作太频繁，请稍后再试。', 429)
            start = row['window_start'] if row and row['window_start'] > now - window else now
            attempts = row['attempts'] + 1 if row and start == row['window_start'] else 1
            db.execute('INSERT INTO reader_rate_limits VALUES (?,?,?) ON CONFLICT(rate_key) DO UPDATE SET '
                       'window_start=excluded.window_start,attempts=excluded.attempts', (key, start, attempts))
            db.execute('DELETE FROM reader_rate_limits WHERE window_start < ?', (now - 86400,))

    def budget(kind, dimensions, *, check_only=False):
        try:
            (check_public if check_only else reserve_public)(app,get_db(),kind,dimensions)
        except LoginLimited as error:
            raise ReaderError('操作太频繁，请稍后再试。',429,error.retry_after)

    @bp.app_context_processor
    def context():
        return {'owner_reader_id': owner_id(get_db()), 'reader_user': current_user(), 'reader_csrf_token': csrf,
                'reader_return': safe_return(request.full_path.rstrip('?')),
                'reader_submission_id': lambda: secrets.token_hex(16)}

    @bp.after_app_request
    def private_dynamic(response):
        if request.path.startswith(('/auth/', '/api/')) or (
                response.mimetype == 'text/html' and not request.path.startswith('/admin')):
            response.headers['Cache-Control'] = 'private, no-store'
            response.vary.add('Cookie')
        return response

    @bp.errorhandler(ReaderError)
    def reader_error(error):
        if request.path.startswith('/api/'):
            next_url = '/'
            if request.view_args and 'post_id' in request.view_args:
                row = get_db().execute('SELECT slug FROM posts WHERE id=?', (request.view_args['post_id'],)).fetchone()
                if row:
                    next_url = url_for('post_detail', slug=row['slug']) + '#comments'
            if request.view_args and 'project_id' in request.view_args:
                row = get_db().execute('SELECT slug FROM projects WHERE id=? AND is_visible=1', (request.view_args['project_id'],)).fetchone()
                if row:
                    next_url = url_for('project_detail', slug=row['slug']) + '#comments'
            result = {'ok': False, 'message': error.message,
                      'login_url': url_for('reader.login', next=next_url)}
            response=app.make_response((result,error.status))
            if error.retry_after: response.headers['Retry-After']=str(error.retry_after)
            return response
        response=app.make_response((render_template('reader_auth.html', mode=request.endpoint.rsplit('.', 1)[-1],
                               error=error.message, next_url=safe_return(request.values.get('next')),
                               nickname=request.form.get('nickname', '')[:24]), error.status))
        if error.retry_after: response.headers['Retry-After']=str(error.retry_after)
        return response

    @bp.errorhandler(sqlite3.OperationalError)
    def database_busy(error):
        get_db().rollback()
        app.logger.exception('Reader database operation failed')
        return reader_error(ReaderError('服务暂时繁忙，请稍后重试；当前输入可以保留。', 503))

    @bp.errorhandler(sqlite3.IntegrityError)
    def concurrent_change(error):
        get_db().rollback()
        return reader_error(ReaderError('内容状态已变化，请刷新后重试。', 409))

    @bp.route('/auth/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            validate_token()
            budget('register',[('global-hour','all',app.config['REGISTER_GLOBAL_HOUR'],3600),
                               ('global-day','all',app.config['REGISTER_GLOBAL_DAY'],86400)],check_only=True)
            throttle('register', 10, 3600)
            nickname = unicodedata.normalize('NFKC', request.form.get('nickname', '')).strip()
            password = request.form.get('password', '')
            if not 1 <= len(nickname) <= 24 or any(unicodedata.category(c).startswith('C') for c in nickname):
                raise ReaderError('昵称请输入 1–24 个字符，不能包含控制字符。')
            if not 8 <= len(password) <= 24:
                raise ReaderError('密码长度需为 8–24 个字符。')
            if password != request.form.get('confirm_password', ''):
                raise ReaderError('两次输入的密码不一致。')
            budget('register',[('global-hour','all',app.config['REGISTER_GLOBAL_HOUR'],3600),
                               ('global-day','all',app.config['REGISTER_GLOBAL_DAY'],86400)])
            password_hash = generate_password_hash(password, method='scrypt')
            db = get_db()
            try:
                with db:
                    row = db.execute('INSERT INTO reader_users(nickname,nickname_key,password_hash,created_at) VALUES(?,?,?,?)',
                                     (nickname, nickname.casefold(), password_hash, now_iso()))
                    user_id = row.lastrowid
            except sqlite3.IntegrityError:
                raise ReaderError('这个昵称已经被使用，请换一个。', 409)
            session['reader_id'] = user_id
            session['reader_version'] = 0
            session['reader_csrf'] = secrets.token_urlsafe(32)
            session.permanent = True
            return redirect(safe_return(request.form.get('next')), code=303)
        return render_template('reader_auth.html', mode='register', error='', nickname='',
                               next_url=safe_return(request.args.get('next')))

    @bp.route('/auth/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            validate_token()
            budget('reader-login',[('global','all',app.config['READER_LOGIN_GLOBAL_WINDOW'],900)],check_only=True)
            raw_nickname = request.form.get('nickname', '')
            password = request.form.get('password', '')
            if len(raw_nickname)>256 or len(password)>128:
                throttle('login',30,900)
                raise ReaderError('昵称或密码不正确。', 401)
            nickname = unicodedata.normalize('NFKC', raw_nickname).strip()
            if len(nickname)>24:
                throttle('login',30,900)
                raise ReaderError('昵称或密码不正确。',401)
            budget('reader-login',[('account',nickname.casefold(),30,900),
                                   ('global','all',app.config['READER_LOGIN_GLOBAL_WINDOW'],900)],check_only=True)
            throttle('login',30,900)
            budget('reader-login',[('account',nickname.casefold(),30,900),
                                   ('global','all',app.config['READER_LOGIN_GLOBAL_WINDOW'],900)])
            user = get_db().execute('SELECT * FROM reader_users WHERE nickname_key=?', (nickname.casefold(),)).fetchone()
            valid = check_password_hash(user['password_hash'] if user else _DUMMY_HASH, password)
            if not user or not valid or user['disabled_at']:
                raise ReaderError('昵称或密码不正确。', 401)
            session['reader_id'] = user['id']
            session['reader_version'] = user['session_version']
            session['reader_csrf'] = secrets.token_urlsafe(32)
            session.permanent = True
            return redirect(safe_return(request.form.get('next')), code=303)
        return render_template('reader_auth.html', mode='login', error='', nickname='',
                               next_url=safe_return(request.args.get('next')))

    @bp.post('/auth/logout')
    def logout():
        validate_token()
        session.pop('reader_id', None)
        session.pop('reader_version', None)
        session.pop('reader_csrf', None)
        # Reader logout deliberately leaves administrator and visitor session keys intact.
        return redirect(safe_return(request.form.get('next')), code=303)

    @bp.get('/api/reader/session')
    def reader_session():
        user = current_user()
        return {'ok': True, 'user': {**dict(user), 'is_owner': user['id'] == owner_id(get_db())} if user else None, 'csrf_token': csrf()}

    @bp.post('/api/posts/<int:post_id>/like')
    def like(post_id):
        user = require_user()
        validate_token()
        published_post(post_id)
        desired = request.form.get('liked')
        if desired not in ('0', '1'):
            raise ReaderError('点赞状态无效。')
        budget('like',[('user',user['id'],app.config['LIKE_USER_MINUTE'],60),
                       ('ip',client_ip(),app.config['LIKE_IP_MINUTE'],60),
                       ('global','all',app.config['LIKE_GLOBAL_MINUTE'],60)])
        db = get_db()
        with db:
            if desired == '1':
                db.execute('INSERT OR IGNORE INTO post_likes VALUES (?,?,?)', (post_id, user['id'], now_iso()))
            else:
                db.execute('DELETE FROM post_likes WHERE post_id=? AND user_id=?', (post_id, user['id']))
            count = db.execute('SELECT COUNT(*) FROM post_likes WHERE post_id=?', (post_id,)).fetchone()[0]
        if request.accept_mimetypes.best == 'application/json':
            return {'ok': True, 'liked': desired == '1', 'like_count': count}
        return redirect(url_for('post_detail', slug=published_post(post_id)['slug']) + '#comments', code=303)

    @bp.route('/api/posts/<int:post_id>/comments', methods=['GET', 'POST'])
    def comments(post_id):
        post = published_post(post_id)
        if request.method == 'GET':
            data = post_data(post_id)
            return {'ok': True, 'comments': data['comments'], 'total': data['comment_count'],
                    'page': data['page'], 'pages': data['pages']}
        user = require_user()
        validate_token()
        throttle('comment-attempt', 100, 3600)
        content = request.form.get('content', '').strip()
        nonce = request.form.get('submission_id', '')
        raw_target = request.form.get('reply_to_id', '')
        if raw_target and (not raw_target.isascii() or not raw_target.isdecimal() or len(raw_target) > 18):
            raise ReaderError('回复目标无效。', 400)
        reply_to_id = int(raw_target) if raw_target else None
        if reply_to_id is not None and reply_to_id < 1:
            raise ReaderError('回复目标无效。', 400)
        if not 1 <= len(content) <= 2000 or '\x00' in content:
            raise ReaderError('评论请输入 1–2000 个字符。')
        if not re.fullmatch('[a-f0-9]{32}', nonce):
            raise ReaderError('提交标识无效，请刷新后重试。', 400)
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            active = db.execute('SELECT disabled_at,session_version FROM reader_users WHERE id=?', (user['id'],)).fetchone()
            if not active or active['disabled_at'] or active['session_version'] != session.get('reader_version', 0):
                raise ReaderError('账号状态已变化，请重新登录。', 401)
            target = db.execute('SELECT id,parent_id FROM post_comments WHERE id=? AND post_id=? AND deleted_at IS NULL AND status="approved"', (reply_to_id, post_id)).fetchone() if reply_to_id else None
            if reply_to_id and not target:
                raise ReaderError('回复的评论已不存在，请刷新后重试。', 404)
            parent_id = (target['parent_id'] or target['id']) if target else None
            old = db.execute('SELECT * FROM post_comments WHERE user_id=? AND submission_id=?', (user['id'], nonce)).fetchone()
            if old:
                if old['post_id'] != post_id or old['content'] != content or old['reply_to_id'] != reply_to_id:
                    raise ReaderError('请刷新后重新提交。', 409)
                comment_id = old['id']
            else:
                reserve_submission(db, user['id'], app.secret_key, ReaderError)
                if user['id'] == owner_id(db):
                    state, reason = 'approved', '博主免审核'
                else:
                    state, reason = classify(db, user['id'], content)
                if state == 'rejected':
                    db.execute('UPDATE reader_users SET violation_count=violation_count+1 WHERE id=?', (user['id'],))
                cursor = db.execute('INSERT INTO post_comments(post_id,user_id,content,created_at,submission_id,parent_id,reply_to_id,status,moderation_reason,reviewed_at,reviewed_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                    (post_id, user['id'], content, now_iso(), nonce, parent_id, reply_to_id, state, reason,
                                     now_iso() if state == 'rejected' else None, 'rules' if state == 'rejected' else None))
                comment_id = cursor.lastrowid
                db.execute('UPDATE post_comments SET content_fingerprint=? WHERE id=?', (fingerprint(content), comment_id))
            db.execute('UPDATE post_comments SET floor=(SELECT COALESCE(MAX(floor),0)+1 FROM post_comments WHERE post_id=?) WHERE id=? AND floor IS NULL', (post_id, comment_id))
            item = comment_row(db, comment_id)
            count = active_count(db, post_id)
            roots = thread_count(db, post_id)
        # The with-db block committed successfully; retries never emit again.
        if old is None and state == 'pending':
            try:
                comment_pending.send(app, comment_id=comment_id, post_id=post_id)
            except Exception:
                # Notification receivers must not turn a saved comment into a failed submission.
                app.logger.exception('Pending comment notification failed: %s', comment_id)
        state = db.execute('SELECT status FROM post_comments WHERE id=?', (comment_id,)).fetchone()[0]
        message = {'pending': '评论已提交，等待管理员审核', 'approved': '评论已发表。', 'rejected': '评论包含违规内容，已拒绝。'}[state]
        if state != 'approved':
            item = None
        post_url = url_for('post_detail', slug=post['slug'])
        if request.accept_mimetypes.best == 'application/json':
            return {'ok': True, 'status': state, 'message': message, 'comment': item, 'comment_count': count, 'thread_count': roots, 'post_url': post_url,
                    'submission_id': secrets.token_hex(16)}, 200 if old else 201
        flash(message, 'success' if state != 'rejected' else 'error')
        return redirect(post_url + '#comments', code=303)

    @bp.post('/api/posts/<int:post_id>/comments/<int:comment_id>/like')
    def comment_like(post_id, comment_id):
        user = require_user()
        validate_token()
        published_post(post_id)
        desired = request.form.get('liked')
        if desired not in ('0', '1'):
            raise ReaderError('点赞状态无效。')
        budget('like',[('user',user['id'],app.config['LIKE_USER_MINUTE'],60),
                       ('ip',client_ip(),app.config['LIKE_IP_MINUTE'],60),
                       ('global','all',app.config['LIKE_GLOBAL_MINUTE'],60)],check_only=True)
        throttle('comment-like', 120, 60)
        budget('like',[('user',user['id'],app.config['LIKE_USER_MINUTE'],60),
                       ('ip',client_ip(),app.config['LIKE_IP_MINUTE'],60),
                       ('global','all',app.config['LIKE_GLOBAL_MINUTE'],60)])
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT c.id FROM post_comments c WHERE c.id=? AND c.post_id=? AND c.deleted_at IS NULL AND ' + public_comment, (comment_id, post_id)).fetchone()
            if not row:
                raise ReaderError('评论不存在或无法点赞。', 404)
            if desired == '1':
                db.execute('INSERT OR IGNORE INTO comment_likes VALUES (?,?,?)', (comment_id, user['id'], now_iso()))
            else:
                db.execute('DELETE FROM comment_likes WHERE comment_id=? AND user_id=?', (comment_id, user['id']))
            count = db.execute('SELECT COUNT(*) FROM comment_likes WHERE comment_id=?', (comment_id,)).fetchone()[0]
        return {'ok': True, 'liked': desired == '1', 'like_count': count}

    @bp.post('/api/posts/<int:post_id>/comments/<int:comment_id>/delete')
    def delete_comment(post_id, comment_id):
        user = require_user()
        validate_token()
        post = published_post(post_id)
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT user_id,deleted_at FROM post_comments WHERE id=? AND post_id=?',
                             (comment_id, post_id)).fetchone()
            if not row:
                raise ReaderError('评论不存在。', 404)
            if not can_delete_comment(user, row['user_id']):
                raise ReaderError('只能删除自己的评论，博主可管理其他人的评论。', 403)
            # Erase the text, retain references so other readers' replies survive.
            if not row['deleted_at']:
                db.execute('UPDATE post_comments SET content=?,deleted_at=? WHERE id=?',
                           ('该评论已删除', now_iso(), comment_id))
            count = active_count(db, post_id)
        if request.accept_mimetypes.best == 'application/json':
            return {'ok': True, 'comment_count': count}
        return redirect(url_for('post_detail', slug=post['slug']) + '#comments', code=303)

    def time_iso_ago(seconds):
        from datetime import datetime, timezone, timedelta
        return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).replace(microsecond=0).isoformat()

    visible_root = "(c.status='approved' AND (c.deleted_at IS NULL OR EXISTS (SELECT 1 FROM post_comments child WHERE child.parent_id=c.id AND child.deleted_at IS NULL AND child.status='approved')))"

    public_comment = "(c.status='approved' AND (c.parent_id IS NULL OR EXISTS (SELECT 1 FROM post_comments root WHERE root.id=c.parent_id AND root.status='approved')) AND (c.reply_to_id IS NULL OR EXISTS (SELECT 1 FROM post_comments target WHERE target.id=c.reply_to_id AND target.status='approved')))"

    def active_count(db, post_id):
        return db.execute('SELECT COUNT(*) FROM post_comments c WHERE c.post_id=? AND c.deleted_at IS NULL AND ' + public_comment, (post_id,)).fetchone()[0]

    def thread_count(db, post_id):
        return db.execute('SELECT COUNT(*) FROM post_comments c WHERE c.post_id=? AND c.parent_id IS NULL AND ' + visible_root, (post_id,)).fetchone()[0]

    # Display floors count only approved, undeleted root comments in chronological order.
    # Compute on read so deletion/moderation closes gaps without rewriting stored identifiers.
    floor_select = ("CASE WHEN c.parent_id IS NULL AND c.deleted_at IS NULL AND c.status='approved' THEN "
                    "(SELECT COUNT(*) FROM post_comments f WHERE f.post_id=c.post_id AND f.parent_id IS NULL "
                    "AND f.deleted_at IS NULL AND f.status='approved' AND "
                    "(f.created_at,f.id)<=(c.created_at,c.id)) ELSE NULL END AS floor,")
    comment_select = ('SELECT c.id,c.post_id,' + floor_select + '(SELECT COUNT(*) FROM comment_likes cl WHERE cl.comment_id=c.id) AS like_count,c.content,c.created_at,c.parent_id,c.reply_to_id,c.deleted_at,u.nickname,u.id AS user_id,u.avatar_key,u.custom_title,'
                      "CASE WHEN target.deleted_at IS NOT NULL THEN '已删除的评论' ELSE ru.nickname END AS reply_to_nickname FROM post_comments c JOIN reader_users u ON c.user_id=u.id "
                      'LEFT JOIN post_comments target ON target.id=c.reply_to_id LEFT JOIN reader_users ru ON ru.id=target.user_id ')

    def comment_delete_allowed(user, author_id, owner):
        return bool(user and (user['id'] == author_id or user['id'] == owner))

    def can_delete_comment(user, author_id):
        return comment_delete_allowed(user, author_id, owner_id(get_db()))

    def serialize_comment(db, row, viewer=None):
        user = current_user()
        owner = owner_id(db) if viewer is None else viewer['owner']
        deleted = bool(row['deleted_at'])
        liked = bool(user and db.execute('SELECT 1 FROM comment_likes WHERE comment_id=? AND user_id=?', (row['id'], user['id'])).fetchone()) if viewer is None else row['id'] in viewer['liked']
        return {**dict(row), 'liked': liked, 'nickname': '已删除' if deleted else row['nickname'],
                'custom_title': '' if deleted else row['custom_title'],
                'is_owner': not deleted and row['user_id'] == owner,
                'avatar_url': None if deleted else avatar_url(row['user_id'], row['avatar_key'], row['user_id'] == owner),
                'can_delete': bool(not deleted and comment_delete_allowed(user,row['user_id'],owner))}

    def serialize_rows(db, rows):
        if not rows:
            return []
        user = current_user()
        liked = set()
        if user and rows:
            marks = ','.join('?' for _ in rows)
            liked = {r['comment_id'] for r in db.execute(
                'SELECT comment_id FROM comment_likes WHERE user_id=? AND comment_id IN ('+marks+')',
                (user['id'],*(r['id'] for r in rows)))}
        viewer = {'owner':owner_id(db),'liked':liked}
        return [serialize_comment(db,row,viewer) for row in rows]

    def comment_row(db, comment_id):
        return serialize_comment(db, db.execute(comment_select + 'WHERE c.id=?', (comment_id,)).fetchone())

    display_reply = public_comment + ' AND (c.deleted_at IS NULL OR EXISTS (SELECT 1 FROM post_comments child WHERE child.reply_to_id=c.id AND child.deleted_at IS NULL AND child.status="approved"))'

    def reply_page(db, post_id, root_id, after=''):
        params=[post_id,root_id]
        where='c.post_id=? AND c.parent_id=? AND '+display_reply
        if after:
            if not re.fullmatch(r'[0-9]{1,18}',after):
                raise ReaderError('回复游标无效。',400)
            cursor=db.execute('SELECT c.created_at,c.id FROM post_comments c WHERE c.id=? AND c.post_id=? AND c.parent_id=? AND '+display_reply,
                              (int(after),post_id,root_id)).fetchone()
            if not cursor: raise ReaderError('回复游标已失效，请刷新评论。',404)
            where+=' AND (c.created_at,c.id)>(?,?)'
            params.extend((cursor['created_at'],cursor['id']))
        rows=db.execute(comment_select+'WHERE '+where+' ORDER BY c.created_at,c.id LIMIT 21',params).fetchall()
        has_more=len(rows)>20
        items=serialize_rows(db,rows[:20])
        cursor=str(items[-1]['id']) if items and has_more else None
        return items,cursor

    @bp.get('/api/posts/<int:post_id>/comments/<int:comment_id>/replies')
    def comment_replies(post_id,comment_id):
        published_post(post_id)
        db=get_db()
        root=db.execute('SELECT c.id FROM post_comments c WHERE c.id=? AND c.post_id=? AND c.parent_id IS NULL AND '+visible_root,
                        (comment_id,post_id)).fetchone()
        if not root: raise ReaderError('评论不存在或不可公开访问。',404)
        items,cursor=reply_page(db,post_id,comment_id,request.args.get('after',''))
        return {'ok':True,'replies':items,'next_cursor':cursor,'root_id':comment_id}

    def post_data(post_id):
        db = get_db()
        user = current_user()
        count = active_count(db, post_id)
        roots = thread_count(db, post_id)
        pages = max(1, math.ceil(roots / PAGE_SIZE))
        try:
            page = max(1, min(pages, int(request.args.get('comments_page', '1'))))
        except ValueError:
            page = 1
        sort = request.args.get('comments_sort', 'newest')
        if sort not in ('newest', 'oldest', 'likes'):
            sort = 'newest'
        ordering = {'newest': 'c.created_at DESC,c.id DESC', 'oldest': 'c.created_at ASC,c.id ASC', 'likes': 'like_count DESC,c.created_at DESC,c.id DESC'}[sort]
        selection = 'c.id'
        if sort == 'likes':
            selection += ',(SELECT COUNT(*) FROM comment_likes cl WHERE cl.comment_id=c.id) AS like_count'
        selected = db.execute('SELECT '+selection+' FROM post_comments c WHERE c.post_id=? AND c.parent_id IS NULL AND '+visible_root+
                              ' ORDER BY '+ordering+' LIMIT ? OFFSET ?',
                              (post_id,PAGE_SIZE,(page-1)*PAGE_SIZE)).fetchall()
        comments=[]
        if selected:
            ids=[r['id'] for r in selected]
            hydrated=db.execute(comment_select+'WHERE c.id IN ('+','.join('?' for _ in ids)+') AND c.post_id=? AND c.parent_id IS NULL AND '+visible_root,
                                (*ids,post_id)).fetchall()
            by_id={r['id']:r for r in hydrated}
            comments=[by_id[i] for i in ids if i in by_id]
        threads = serialize_rows(db,comments)
        for row in threads:
            after=request.args.get('reply_after','') if request.args.get('reply_thread')==str(row['id']) else ''
            row['replies'],row['next_reply_cursor']=reply_page(db,post_id,row['id'],after)
            row['replies_url']=url_for('reader.comment_replies',post_id=post_id,comment_id=row['id'])
        return {'like_count': db.execute('SELECT COUNT(*) FROM post_likes WHERE post_id=?', (post_id,)).fetchone()[0],
                'liked': bool(user and db.execute('SELECT 1 FROM post_likes WHERE post_id=? AND user_id=?', (post_id, user['id'])).fetchone()),
                'sort': sort, 'comments': threads, 'comment_count': count, 'thread_count': roots, 'page': page, 'pages': pages}

    def record_view(post_id):
        if request.method != 'GET' or classify_client(request.headers.get('User-Agent', ''))['is_bot']:
            return
        visitor = visitor_fingerprint(False)
        if not visitor:
            return
        keys = ['browser:' + visitor]
        user = current_user()
        if user:
            keys.append('user:' + str(user['id']))
        db, now = get_db(), int(time.time())
        query = f'SELECT last_counted_at FROM post_view_visitors WHERE post_id=? AND visitor_key IN ({",".join("?" for _ in keys)})'
        rows = db.execute(query, (post_id, *keys)).fetchall()
        last = max((row['last_counted_at'] for row in rows), default=0)
        if len(rows) == len(keys) and last > now - VIEW_WINDOW and all(row['last_counted_at'] == last for row in rows):
            return
        with db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute(query, (post_id, *keys)).fetchall()
            last = max((r['last_counted_at'] for r in rows), default=0)
            if last <= now - VIEW_WINDOW:
                changed = db.execute("UPDATE posts SET views=views+1 WHERE id=? AND status='published'", (post_id,)).rowcount
                if not changed:
                    return
                last = now
            for key in keys:
                db.execute('INSERT INTO post_view_visitors VALUES(?,?,?) ON CONFLICT(post_id,visitor_key) '
                           'DO UPDATE SET last_counted_at=excluded.last_counted_at '
                           'WHERE post_view_visitors.last_counted_at!=excluded.last_counted_at', (post_id, key, last))

    def record_project_view(project_id):
        if request.method != 'GET' or classify_client(request.headers.get('User-Agent', ''))['is_bot']:
            return
        visitor = visitor_fingerprint(False)
        if not visitor:
            return
        keys = ['browser:' + visitor]
        user = current_user()
        if user:
            keys.append('user:' + str(user['id']))
        db, now = get_db(), int(time.time())
        query = f'SELECT last_counted_at FROM project_view_visitors WHERE project_id=? AND visitor_key IN ({",".join("?" for _ in keys)})'
        rows = db.execute(query, (project_id, *keys)).fetchall()
        last = max((row['last_counted_at'] for row in rows), default=0)
        if len(rows) == len(keys) and last > now - VIEW_WINDOW and all(row['last_counted_at'] == last for row in rows):
            return
        with db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute(query, (project_id, *keys)).fetchall()
            last = max((r['last_counted_at'] for r in rows), default=0)
            db.execute('INSERT OR IGNORE INTO project_view_counts(project_id,views) '
                       'SELECT id,0 FROM projects WHERE id=? AND is_visible=1', (project_id,))
            if last <= now - VIEW_WINDOW:
                changed = db.execute("UPDATE project_view_counts SET views=views+1 WHERE project_id=? AND EXISTS (SELECT 1 FROM projects WHERE id=project_id AND is_visible=1)", (project_id,)).rowcount
                if not changed:
                    return
                last = now
            for key in keys:
                db.execute('INSERT INTO project_view_visitors VALUES(?,?,?) ON CONFLICT(project_id,visitor_key) '
                           'DO UPDATE SET last_counted_at=excluded.last_counted_at '
                           'WHERE project_view_visitors.last_counted_at!=excluded.last_counted_at', (project_id, key, last))

    from project_community import install_project_community
    project_data=install_project_community(app,get_db,now_iso,bp,(current_user,require_user,validate_token,throttle,budget))
    app.extensions['community'] = {'record_view': record_view, 'record_project_view': record_project_view, 'post_data': post_data, 'project_data': project_data}
    app.register_blueprint(bp)
    install_owner(app, get_db, now_iso)
    from accounts import install_accounts
    install_accounts(app, get_db, now_iso)
    install_moderation(app, get_db, now_iso)
    install_profile(app, get_db, now_iso, current_user, csrf, validate_token, throttle, ReaderError)
