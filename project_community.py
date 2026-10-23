"""Project discussions using the same reader identity, CSRF and request budgets as articles."""
from pathlib import Path
import math,re,secrets
from flask import request,session,redirect,url_for,flash
from owner import owner_id
from reader_profile import avatar_url
from moderation import reserve_submission,classify,client_ip,fingerprint,comment_pending

def init_project_community(db):
    schema=Path(__file__).with_name('migrations')/'20261022_project_community.sql'
    for statement in schema.read_text(encoding='utf-8').split(';'):
        if statement.strip():db.execute(statement)

def install_project_community(app,get_db,now_iso,bp,security):
    from community import ReaderError,PAGE_SIZE
    current_user,require_user,validate_token,throttle,budget=security
    def visible_project(project_id):
        row=get_db().execute('SELECT id,slug FROM projects WHERE id=? AND is_visible=1',(project_id,)).fetchone()
        if not row:raise ReaderError('项目不存在或暂不可访问。',404)
        return row

    @bp.post('/api/projects/<int:project_id>/like')
    def project_like(project_id):
        user = require_user()
        validate_token()
        visible_project(project_id)
        desired = request.form.get('liked')
        if desired not in ('0', '1'):
            raise ReaderError('点赞状态无效。')
        budget('like',[('user',user['id'],app.config['LIKE_USER_MINUTE'],60),
                       ('ip',client_ip(),app.config['LIKE_IP_MINUTE'],60),
                       ('global','all',app.config['LIKE_GLOBAL_MINUTE'],60)])
        db = get_db()
        with db:
            if desired == '1':
                db.execute('INSERT OR IGNORE INTO project_likes VALUES (?,?,?)', (project_id, user['id'], now_iso()))
            else:
                db.execute('DELETE FROM project_likes WHERE project_id=? AND user_id=?', (project_id, user['id']))
            count = db.execute('SELECT COUNT(*) FROM project_likes WHERE project_id=?', (project_id,)).fetchone()[0]
        if request.accept_mimetypes.best == 'application/json':
            return {'ok': True, 'liked': desired == '1', 'like_count': count}
        return redirect(url_for('project_detail', slug=visible_project(project_id)['slug']) + '#comments', code=303)

    @bp.route('/api/projects/<int:project_id>/comments', methods=['GET', 'POST'])
    def project_comments(project_id):
        post = visible_project(project_id)
        if request.method == 'GET':
            data = project_data(project_id)
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
            target = db.execute('SELECT id,parent_id FROM project_comments WHERE id=? AND project_id=? AND deleted_at IS NULL AND status="approved"', (reply_to_id, project_id)).fetchone() if reply_to_id else None
            if reply_to_id and not target:
                raise ReaderError('回复的评论已不存在，请刷新后重试。', 404)
            parent_id = (target['parent_id'] or target['id']) if target else None
            old = db.execute('SELECT * FROM project_comments WHERE user_id=? AND submission_id=?', (user['id'], nonce)).fetchone()
            if old:
                if old['project_id'] != project_id or old['content'] != content or old['reply_to_id'] != reply_to_id:
                    raise ReaderError('请刷新后重新提交。', 409)
                comment_id = old['id']
            else:
                reserve_submission(db, user['id'], app.secret_key, ReaderError)
                if user['id'] == owner_id(db):
                    state, reason = 'approved', '博主免审核'
                else:
                    state, reason = classify(db, user['id'], content, comment_table='project_comments')
                if state == 'rejected':
                    db.execute('UPDATE reader_users SET violation_count=violation_count+1 WHERE id=?', (user['id'],))
                cursor = db.execute('INSERT INTO project_comments(project_id,user_id,content,created_at,submission_id,parent_id,reply_to_id,status,moderation_reason,reviewed_at,reviewed_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                    (project_id, user['id'], content, now_iso(), nonce, parent_id, reply_to_id, state, reason,
                                     now_iso() if state == 'rejected' else None, 'rules' if state == 'rejected' else None))
                comment_id = cursor.lastrowid
                db.execute('UPDATE project_comments SET content_fingerprint=? WHERE id=?', (fingerprint(content), comment_id))
            db.execute('UPDATE project_comments SET floor=(SELECT COALESCE(MAX(floor),0)+1 FROM project_comments WHERE project_id=?) WHERE id=? AND floor IS NULL', (project_id, comment_id))
            item = comment_row(db, comment_id)
            count = active_count(db, project_id)
            roots = thread_count(db, project_id)
        # The with-db block committed successfully; retries never emit again.
        if old is None and state == 'pending':
            try:
                comment_pending.send(app, comment_id=comment_id, project_id=project_id, content_kind='project')
            except Exception:
                # Notification receivers must not turn a saved comment into a failed submission.
                app.logger.exception('Pending comment notification failed: %s', comment_id)
        state = db.execute('SELECT status FROM project_comments WHERE id=?', (comment_id,)).fetchone()[0]
        message = {'pending': '评论已提交，等待管理员审核', 'approved': '评论已发表。', 'rejected': '评论包含违规内容，已拒绝。'}[state]
        if state != 'approved':
            item = None
        post_url = url_for('project_detail', slug=post['slug'])
        if request.accept_mimetypes.best == 'application/json':
            return {'ok': True, 'status': state, 'message': message, 'comment': item, 'comment_count': count, 'thread_count': roots, 'post_url': post_url,
                    'submission_id': secrets.token_hex(16)}, 200 if old else 201
        flash(message, 'success' if state != 'rejected' else 'error')
        return redirect(post_url + '#comments', code=303)

    @bp.post('/api/projects/<int:project_id>/comments/<int:comment_id>/like')
    def project_comment_like(project_id, comment_id):
        user = require_user()
        validate_token()
        visible_project(project_id)
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
            row = db.execute('SELECT c.id FROM project_comments c WHERE c.id=? AND c.project_id=? AND c.deleted_at IS NULL AND ' + public_comment, (comment_id, project_id)).fetchone()
            if not row:
                raise ReaderError('评论不存在或无法点赞。', 404)
            if desired == '1':
                db.execute('INSERT OR IGNORE INTO project_comment_likes VALUES (?,?,?)', (comment_id, user['id'], now_iso()))
            else:
                db.execute('DELETE FROM project_comment_likes WHERE comment_id=? AND user_id=?', (comment_id, user['id']))
            count = db.execute('SELECT COUNT(*) FROM project_comment_likes WHERE comment_id=?', (comment_id,)).fetchone()[0]
        return {'ok': True, 'liked': desired == '1', 'like_count': count}

    @bp.post('/api/projects/<int:project_id>/comments/<int:comment_id>/delete')
    def project_delete_comment(project_id, comment_id):
        user = require_user()
        validate_token()
        post = visible_project(project_id)
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT user_id,deleted_at FROM project_comments WHERE id=? AND project_id=?',
                             (comment_id, project_id)).fetchone()
            if not row:
                raise ReaderError('评论不存在。', 404)
            if not can_delete_comment(user, row['user_id']):
                raise ReaderError('只能删除自己的评论，博主可管理其他人的评论。', 403)
            # Erase the text, retain references so other readers' replies survive.
            if not row['deleted_at']:
                db.execute('UPDATE project_comments SET content=?,deleted_at=? WHERE id=?',
                           ('该评论已删除', now_iso(), comment_id))
            count = active_count(db, project_id)
        if request.accept_mimetypes.best == 'application/json':
            return {'ok': True, 'comment_count': count}
        return redirect(url_for('project_detail', slug=post['slug']) + '#comments', code=303)

    def time_iso_ago(seconds):
        from datetime import datetime, timezone, timedelta
        return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).replace(microsecond=0).isoformat()

    visible_root = "(c.status='approved' AND (c.deleted_at IS NULL OR EXISTS (SELECT 1 FROM project_comments child WHERE child.parent_id=c.id AND child.deleted_at IS NULL AND child.status='approved')))"

    public_comment = "(c.status='approved' AND (c.parent_id IS NULL OR EXISTS (SELECT 1 FROM project_comments root WHERE root.id=c.parent_id AND root.status='approved')) AND (c.reply_to_id IS NULL OR EXISTS (SELECT 1 FROM project_comments target WHERE target.id=c.reply_to_id AND target.status='approved')))"

    def active_count(db, project_id):
        return db.execute('SELECT COUNT(*) FROM project_comments c WHERE c.project_id=? AND c.deleted_at IS NULL AND ' + public_comment, (project_id,)).fetchone()[0]

    def thread_count(db, project_id):
        return db.execute('SELECT COUNT(*) FROM project_comments c WHERE c.project_id=? AND c.parent_id IS NULL AND ' + visible_root, (project_id,)).fetchone()[0]

    # Display floors count only approved, undeleted root comments in chronological order.
    # Compute on read so deletion/moderation closes gaps without rewriting stored identifiers.
    floor_select = ("CASE WHEN c.parent_id IS NULL AND c.deleted_at IS NULL AND c.status='approved' THEN "
                    "(SELECT COUNT(*) FROM project_comments f WHERE f.project_id=c.project_id AND f.parent_id IS NULL "
                    "AND f.deleted_at IS NULL AND f.status='approved' AND "
                    "(f.created_at,f.id)<=(c.created_at,c.id)) ELSE NULL END AS floor,")
    comment_select = ('SELECT c.id,c.project_id,' + floor_select + '(SELECT COUNT(*) FROM project_comment_likes cl WHERE cl.comment_id=c.id) AS like_count,c.content,c.created_at,c.parent_id,c.reply_to_id,c.deleted_at,u.nickname,u.id AS user_id,u.avatar_key,u.custom_title,'
                      "CASE WHEN target.deleted_at IS NOT NULL THEN '已删除的评论' ELSE ru.nickname END AS reply_to_nickname FROM project_comments c JOIN reader_users u ON c.user_id=u.id "
                      'LEFT JOIN project_comments target ON target.id=c.reply_to_id LEFT JOIN reader_users ru ON ru.id=target.user_id ')

    def comment_delete_allowed(user, author_id, owner):
        return bool(user and (user['id'] == author_id or user['id'] == owner))

    def can_delete_comment(user, author_id):
        return comment_delete_allowed(user, author_id, owner_id(get_db()))

    def serialize_comment(db, row, viewer=None):
        user = current_user()
        owner = owner_id(db) if viewer is None else viewer['owner']
        deleted = bool(row['deleted_at'])
        liked = bool(user and db.execute('SELECT 1 FROM project_comment_likes WHERE comment_id=? AND user_id=?', (row['id'], user['id'])).fetchone()) if viewer is None else row['id'] in viewer['liked']
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
                'SELECT comment_id FROM project_comment_likes WHERE user_id=? AND comment_id IN ('+marks+')',
                (user['id'],*(r['id'] for r in rows)))}
        viewer = {'owner':owner_id(db),'liked':liked}
        return [serialize_comment(db,row,viewer) for row in rows]

    def comment_row(db, comment_id):
        return serialize_comment(db, db.execute(comment_select + 'WHERE c.id=?', (comment_id,)).fetchone())

    display_reply = public_comment + ' AND (c.deleted_at IS NULL OR EXISTS (SELECT 1 FROM project_comments child WHERE child.reply_to_id=c.id AND child.deleted_at IS NULL AND child.status="approved"))'

    def reply_page(db, project_id, root_id, after=''):
        params=[project_id,root_id]
        where='c.project_id=? AND c.parent_id=? AND '+display_reply
        if after:
            if not re.fullmatch(r'[0-9]{1,18}',after):
                raise ReaderError('回复游标无效。',400)
            cursor=db.execute('SELECT c.created_at,c.id FROM project_comments c WHERE c.id=? AND c.project_id=? AND c.parent_id=? AND '+display_reply,
                              (int(after),project_id,root_id)).fetchone()
            if not cursor: raise ReaderError('回复游标已失效，请刷新评论。',404)
            where+=' AND (c.created_at,c.id)>(?,?)'
            params.extend((cursor['created_at'],cursor['id']))
        rows=db.execute(comment_select+'WHERE '+where+' ORDER BY c.created_at,c.id LIMIT 21',params).fetchall()
        has_more=len(rows)>20
        items=serialize_rows(db,rows[:20])
        cursor=str(items[-1]['id']) if items and has_more else None
        return items,cursor

    @bp.get('/api/projects/<int:project_id>/comments/<int:comment_id>/replies')
    def project_comment_replies(project_id,comment_id):
        visible_project(project_id)
        db=get_db()
        root=db.execute('SELECT c.id FROM project_comments c WHERE c.id=? AND c.project_id=? AND c.parent_id IS NULL AND '+visible_root,
                        (comment_id,project_id)).fetchone()
        if not root: raise ReaderError('评论不存在或不可公开访问。',404)
        items,cursor=reply_page(db,project_id,comment_id,request.args.get('after',''))
        return {'ok':True,'replies':items,'next_cursor':cursor,'root_id':comment_id}

    def project_data(project_id):
        db = get_db()
        user = current_user()
        count = active_count(db, project_id)
        roots = thread_count(db, project_id)
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
            selection += ',(SELECT COUNT(*) FROM project_comment_likes cl WHERE cl.comment_id=c.id) AS like_count'
        selected = db.execute('SELECT '+selection+' FROM project_comments c WHERE c.project_id=? AND c.parent_id IS NULL AND '+visible_root+
                              ' ORDER BY '+ordering+' LIMIT ? OFFSET ?',
                              (project_id,PAGE_SIZE,(page-1)*PAGE_SIZE)).fetchall()
        comments=[]
        if selected:
            ids=[r['id'] for r in selected]
            hydrated=db.execute(comment_select+'WHERE c.id IN ('+','.join('?' for _ in ids)+') AND c.project_id=? AND c.parent_id IS NULL AND '+visible_root,
                                (*ids,project_id)).fetchall()
            by_id={r['id']:r for r in hydrated}
            comments=[by_id[i] for i in ids if i in by_id]
        threads = serialize_rows(db,comments)
        for row in threads:
            after=request.args.get('reply_after','') if request.args.get('reply_thread')==str(row['id']) else ''
            row['replies'],row['next_reply_cursor']=reply_page(db,project_id,row['id'],after)
            row['replies_url']=url_for('reader.project_comment_replies',project_id=project_id,comment_id=row['id'])
        return {'like_count': db.execute('SELECT COUNT(*) FROM project_likes WHERE project_id=?', (project_id,)).fetchone()[0],
                'liked': bool(user and db.execute('SELECT 1 FROM project_likes WHERE project_id=? AND user_id=?', (project_id, user['id'])).fetchone()),
                'sort': sort, 'comments': threads, 'comment_count': count, 'thread_count': roots, 'page': page, 'pages': pages}

    return project_data


def snapshot_project_community(db, project_id):
    result = {table: [dict(row) for row in db.execute(
        f'SELECT * FROM {table} WHERE project_id=?', (project_id,))]
        for table in ('project_likes', 'project_comments')}
    result['project_comment_likes'] = [dict(row) for row in db.execute('SELECT l.* FROM project_comment_likes l JOIN project_comments c ON c.id=l.comment_id WHERE c.project_id=?', (project_id,))]
    return result


def restore_project_community(db, project_id, payload):
    fields = {
        'project_likes': ('user_id', 'created_at'),
        'project_comments': ('user_id', 'content', 'created_at', 'submission_id', 'deleted_at', 'status', 'reviewed_at', 'reviewed_by', 'moderation_reason', 'manual_approved', 'content_fingerprint', 'floor'),
    }
    for table, columns in fields.items():
        comment_ids = {}
        for row in payload.get(table, []):
            if 'user_id' in columns and not db.execute(
                    'SELECT 1 FROM reader_users WHERE id=?', (row.get('user_id'),)).fetchone():
                continue
            names = ('project_id',) + columns
            inserted = db.execute(f'INSERT OR IGNORE INTO {table} ({",".join(names)}) '
                       f'VALUES ({",".join("?" for _ in names)})',
                       (project_id, *(row.get(key, {'status': 'pending', 'moderation_reason': '', 'manual_approved': 0, 'content_fingerprint': fingerprint(row['content'])}.get(key)) if key in ('deleted_at','status','reviewed_at','reviewed_by','moderation_reason','manual_approved','content_fingerprint','floor') else row[key] for key in columns)))
            if table == 'project_comments' and 'id' in row:
                restored = db.execute('SELECT id FROM project_comments WHERE user_id=? AND submission_id=?',
                                      (row['user_id'], row['submission_id'])).fetchone()
                if restored:
                    comment_ids[row['id']] = restored['id']
        if table == 'project_comments':
            for row in payload.get(table, []):
                if row.get('id') in comment_ids:
                    db.execute('UPDATE project_comments SET parent_id=?,reply_to_id=? WHERE id=?',
                               (comment_ids.get(row.get('parent_id')), comment_ids.get(row.get('reply_to_id')), comment_ids[row['id']]))
            for vote in payload.get('project_comment_likes', []):
                if vote['comment_id'] in comment_ids and db.execute('SELECT 1 FROM reader_users WHERE id=?', (vote['user_id'],)).fetchone():
                    db.execute('INSERT OR IGNORE INTO project_comment_likes VALUES (?,?,?)', (comment_ids[vote['comment_id']], vote['user_id'], vote['created_at']))
            for row in db.execute('SELECT id FROM project_comments WHERE project_id=? AND floor IS NULL ORDER BY created_at,id', (project_id,)).fetchall():
                db.execute('UPDATE project_comments SET floor=(SELECT COALESCE(MAX(floor),0)+1 FROM project_comments WHERE project_id=?) WHERE id=?', (project_id, row['id']))


