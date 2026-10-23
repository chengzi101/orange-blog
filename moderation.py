"""Local comment moderation. All writes share SQLite's submission transaction."""
from blinker import Signal

# Emitted once after a new pending comment transaction commits.
comment_pending = Signal("comment-pending")

import hashlib
import hmac
import ipaddress
import json
import math
import re
import time
import unicodedata
from functools import wraps

from flask import abort, flash, redirect, render_template, request, session, url_for
from security_helpers import constant_time_text_equal
from auth_security import revoke_reader_sessions

DEFAULT_RULES = {'reject_words': ['买卖银行卡', '出售身份证', '代开发票'],
                 'review_words': ['加微信', '加QQ', '兼职日结', '刷单', '博彩', '返利', '贷款'],
                 'blocked_domains': []}


def init_moderation(db):
    additions = {
        'post_comments': {'status': "TEXT NOT NULL DEFAULT 'approved' CHECK(status IN ('pending','approved','rejected'))",
                          'reviewed_at': 'TEXT', 'reviewed_by': 'TEXT',
                          'moderation_reason': "TEXT NOT NULL DEFAULT ''",
                          'manual_approved': 'INTEGER NOT NULL DEFAULT 0',
                          'content_fingerprint': "TEXT NOT NULL DEFAULT ''"},
        'reader_users': {'reviewed_approvals': 'INTEGER NOT NULL DEFAULT 0',
                         'violation_count': 'INTEGER NOT NULL DEFAULT 0'},
    }
    for table, fields in additions.items():
        columns = {r[1] for r in db.execute('PRAGMA table_info(' + table + ')')}
        for name, declaration in fields.items():
            if name not in columns:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
    db.execute('CREATE INDEX IF NOT EXISTS idx_comments_moderation ON post_comments(status,created_at,id)')
    for row in db.execute("SELECT id,content FROM post_comments WHERE content_fingerprint=''").fetchall():
        db.execute('UPDATE post_comments SET content_fingerprint=? WHERE id=?', (fingerprint(row['content']), row['id']))
    db.execute('CREATE INDEX IF NOT EXISTS idx_comments_fingerprint ON post_comments(content_fingerprint,created_at)')
    db.execute('CREATE TABLE IF NOT EXISTS comment_submissions (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, ip_key TEXT NOT NULL, submitted_at INTEGER NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_submissions_user ON comment_submissions(user_id,submitted_at)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_submissions_ip ON comment_submissions(ip_key,submitted_at)')


def client_ip():
    # Only our local reverse proxy can supply a trusted X-Real-IP.
    peer = request.remote_addr or 'unknown'
    try:
        if ipaddress.ip_address(peer).is_loopback:
            return str(ipaddress.ip_address(request.headers.get('X-Real-IP', peer)))
        return str(ipaddress.ip_address(peer))
    except ValueError:
        return peer


def ip_key(secret):
    return hmac.new(str(secret).encode(), client_ip().encode(), hashlib.sha256).hexdigest()


def reserve_submission(db, user_id, secret, error):
    now, key = int(time.time()), ip_key(secret)
    row = db.execute('SELECT COUNT(*) AS n, MAX(submitted_at) AS last FROM comment_submissions WHERE user_id=? AND submitted_at>?', (user_id, now-3600)).fetchone()
    if row['last'] is not None and row['last'] > now-30:
        raise error('评论发送较快，请等待 30 秒再试。', 429)
    if row['n'] >= 20:
        raise error('每小时最多提交 20 条评论，请稍后再试。', 429)
    rows = db.execute('SELECT submitted_at FROM comment_submissions WHERE ip_key=? AND submitted_at>?', (key, now-3600)).fetchall()
    if len(rows) >= 60 or sum(r[0] > now-60 for r in rows) >= 10:
        raise error('当前网络评论请求过于频繁，请稍后再试。', 429)
    db.execute('INSERT INTO comment_submissions(user_id,ip_key,submitted_at) VALUES(?,?,?)', (user_id, key, now))
    db.execute('DELETE FROM comment_submissions WHERE submitted_at<=?', (now-86400,))


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold()
                   if not unicodedata.category(c).startswith(('C', 'Z')))


def fingerprint(content):
    return hashlib.sha256(normalized(content).encode()).hexdigest()


def rules(db):
    row = db.execute("SELECT value FROM settings WHERE key='comment_moderation_rules'").fetchone()
    return json.loads(row[0]) if row else DEFAULT_RULES.copy()


def classify(db, user_id, content, *, comment_table='post_comments'):
    if comment_table not in ('post_comments','project_comments'):raise ValueError('Invalid comment table')
    config, compact = rules(db), normalized(content)
    if any(normalized(w) in compact for w in config['reject_words']):
        return 'rejected', '命中禁止词'
    if re.search(r'<\s*(script|iframe|object|embed|svg)\b|javascript\s*:|on\w+\s*=', compact):
        return 'rejected', '危险脚本或 HTML'
    if any(domain.casefold() in compact for domain in config['blocked_domains']):
        return 'rejected', '禁止的广告域名'
    repeated = bool(re.search(r'(.)\1{11,}|(.{2,12})\2{7,}', compact))
    if re.search(r'(.)\1{49,}|(.{2,12})\2{24,}', compact):
        return 'rejected', '大量重复内容'
    if db.execute(f"SELECT 1 FROM {comment_table} WHERE content_fingerprint=? AND (user_id=? OR datetime(created_at)>=datetime('now','-1 day')) LIMIT 1", (fingerprint(content), user_id)).fetchone():
        return 'pending', '重复评论'
    if repeated:
        return 'pending', '重复字符或片段'
    if re.search(r'https?://|www\.|[a-z0-9-]+\.(?:com|cn|net|org|top|xyz|vip|club|io|me)\b', compact):
        return 'pending', '包含外部链接'
    if any(normalized(w) in compact for w in config['review_words']):
        return 'pending', '命中待审词'
    user = db.execute('SELECT reviewed_approvals,violation_count FROM reader_users WHERE id=?', (user_id,)).fetchone()
    return ('approved', '可信用户') if user['reviewed_approvals'] >= 3 and not user['violation_count'] else ('pending', '新用户或有违规记录')


def install_moderation(app, get_db, now_iso):
    def admin(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get('admin_authenticated'):
                abort(403)
            if request.method == 'POST':
                expected = session.get('csrf_token', '')
                if not expected or not constant_time_text_equal(expected, request.form.get('csrf_token', '')):
                    abort(400)
            return view(*args, **kwargs)
        return wrapped

    @app.after_request
    def no_cache(response):
        if request.path.startswith('/admin/comments'):
            response.headers['Cache-Control'] = 'private, no-store'
            response.vary.add('Cookie')
        return response

    def audit(db, action, detail):
        db.execute('INSERT INTO activity_log(action,item_type,item_title,detail,created_at) VALUES(?,?,?,?,?)',
                   (action, 'comment', '评论审核', detail, now_iso()))
        db.execute('DELETE FROM activity_log WHERE id NOT IN (SELECT id FROM activity_log ORDER BY id DESC LIMIT 300)')

    def comment_scope():
        scope=request.values.get("scope","posts")
        if scope not in ("posts","projects"):abort(400)
        return scope, ("project_comments" if scope=="projects" else "post_comments")

    def pending_count():
        return get_db().execute(
            "SELECT (SELECT COUNT(*) FROM post_comments WHERE status='pending' AND deleted_at IS NULL) + (SELECT COUNT(*) FROM project_comments WHERE status='pending' AND deleted_at IS NULL)"
        ).fetchone()[0]

    @app.context_processor
    def comment_notification_context():
        if session.get('admin_authenticated') and request.path.startswith('/admin'):
            return {'pending_comment_count': pending_count()}
        return {}

    @app.get('/admin/comments/pending-count')
    @admin
    def pending_comment_count():
        return {'ok': True, 'pending_count': pending_count()}

    @app.get('/admin/comments')
    @admin
    def admin_comments():
        db = get_db()
        status = request.args.get('status', 'pending')
        if status not in ('pending', 'approved', 'rejected'):
            abort(400)
        scope,table=comment_scope()
        total = db.execute(f'SELECT COUNT(*) FROM {table} WHERE status=?', (status,)).fetchone()[0]
        pages = max(1, math.ceil(total/20))
        try:
            page = max(1, min(pages, int(request.args.get('page', 1))))
        except ValueError:
            page = 1
        items,column=('projects','project_id') if scope=='projects' else ('posts','post_id')
        comments = db.execute(f'SELECT c.*,u.nickname,u.disabled_at,p.title,p.slug FROM {table} c JOIN reader_users u ON u.id=c.user_id JOIN {items} p ON p.id=c.{column} WHERE c.status=? ORDER BY c.id DESC LIMIT 20 OFFSET ?', (status, (page-1)*20)).fetchall()
        return render_template('admin_comments.html', comments=comments, scope=scope, status=status, page=page, pages=pages, total=total)

    @app.post('/admin/comments/moderate')
    @admin
    def moderate_comments():
        scope,table=comment_scope()
        values = request.form.getlist('comment_id')
        action = request.form.get('action')
        if action not in ('approve', 'reject', 'delete') or not 1 <= len(values) <= 100 or any(not re.fullmatch(r'[0-9]{1,18}', v) for v in values):
            abort(400)
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            for cid in set(map(int, values)):
                row = db.execute(f'SELECT * FROM {table} WHERE id=?', (cid,)).fetchone()
                if not row:
                    abort(404)
                if action == 'delete':
                    db.execute(f"UPDATE {table} SET content='该评论已删除',deleted_at=COALESCE(deleted_at,?) WHERE id=?", (now_iso(), cid))
                elif not row['deleted_at']:
                    if action == 'approve' and not row['manual_approved']:
                        db.execute('UPDATE reader_users SET reviewed_approvals=reviewed_approvals+1 WHERE id=?', (row['user_id'],))
                    if action == 'reject' and row['status'] != 'rejected':
                        db.execute('UPDATE reader_users SET violation_count=violation_count+1 WHERE id=?', (row['user_id'],))
                    db.execute(f'UPDATE {table} SET status=?,reviewed_at=?,reviewed_by=?,moderation_reason=?,manual_approved=MAX(manual_approved,?) WHERE id=?',
                               ('approved' if action == 'approve' else 'rejected', now_iso(), 'admin', '管理员通过' if action == 'approve' else '管理员拒绝', int(action == 'approve'), cid))
            audit(db, '评论批量操作', scope + ':' + action + ':' + ','.join(values))
        flash('评论操作已保存。', 'success')
        return redirect(url_for('admin_comments',scope=request.values.get('scope','posts')), code=303)

    @app.post('/admin/comments/users/<int:user_id>/<action>')
    @admin
    def comment_user_ban(user_id, action):
        from owner import owner_id
        if action not in ('ban', 'unban'):
            abort(400)
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM reader_users WHERE id=?', (user_id,)).fetchone():
                abort(404)
            if action == 'ban' and user_id == owner_id(db):
                abort(409, '请先更换博主账号绑定。')
            db.execute('UPDATE reader_users SET disabled_at=?,session_version=session_version+1,violation_count=violation_count+? WHERE id=?', (now_iso() if action == 'ban' else None, int(action == 'ban'), user_id))
            db.execute('DELETE FROM owner_login_tickets WHERE user_id=?', (user_id,))
            revoke_reader_sessions(db, user_id)
            audit(db, '评论用户封禁操作', action + ':' + str(user_id))
        return redirect(url_for('admin_comments',scope=request.values.get('scope','posts')), code=303)

    @app.route('/admin/comments/rules', methods=['GET', 'POST'])
    @admin
    def comment_rules():
        db = get_db()
        if request.method == 'POST':
            config = {key: list(dict.fromkeys(line.strip() for line in request.form.get(key, '').splitlines() if line.strip())) for key in DEFAULT_RULES}
            if any(len(items)>200 or any(len(w)>100 for w in items) for items in config.values()):
                abort(400, '每类最多200项，每项最多100字。')
            with db:
                db.execute("INSERT INTO settings(key,value) VALUES('comment_moderation_rules',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(config, ensure_ascii=False),))
                audit(db, '修改评论过滤规则', '本地规则更新')
            flash('过滤规则已保存。', 'success')
            return redirect(url_for('comment_rules'), code=303)
        return render_template('admin_comment_rules.html', rules=rules(db))
