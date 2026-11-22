"""Game sign-in against the reader account store, via the authenticated backend."""
import hmac, re, secrets, time, unicodedata, sqlite3
from flask import request, g
from unified_sms import AccountError, digest
from unified_schema import complete
from unified_passwords import check_password_hash, generate_password_hash, validate_username, validate_password

POLICY_VERSION = '2026-10-28'
PHONE_ONLY = '!phone-only'


def install_phone_login(app, get_db, now_iso, sms):
    dummy_hash = generate_password_hash(secrets.token_urlsafe(32))
    def proof():
        auth = request.authorization
        if not auth or auth.type.lower() != 'basic' or auth.username != 'pixel-ybu' or not hmac.compare_digest(auth.password or '', app.config['UNIFIED_GAME_SECRET']):
            raise AccountError('无效的服务凭据。', 401)
        binding = request.headers.get('X-Game-Login-Key', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', binding):
            raise AccountError('登录页面已过期，请重新打开。', 400)
        # Never accept a browser-supplied user id or an existing reader session.
        g.unified_game_sms_binding = binding
        data = request.get_json(silent=True)
        if not isinstance(data, dict): raise AccountError('请求格式无效。', 400)
        if data.get('agreed') is not True or data.get('policy_version') != POLICY_VERSION:
            raise AccountError('请阅读并同意当前版本的用户协议和隐私政策。', 422)
        return data

    @app.post('/internal/game/register/send')
    def unified_game_register_send():
        data = proof()
        return sms.send(data.get('phone', ''), 'register')

    @app.post('/internal/game/register')
    def unified_game_register():
        data = proof()
        from moderation import client_ip
        sms.reserve('unified-register', [('ip', client_ip(), 10, 3600), ('global', 'all', 100, 86400)])
        try:
            nickname = validate_username(data.get('username', ''))
            password = validate_password(data.get('password', ''))
        except (ValueError, TypeError):
            raise AccountError('用户名需为1–24个汉字、字母、数字、下划线或短横线；密码需为10–128位，字母、数字、符号至少两类。')
        phone, cid = data.get('phone', ''), data.get('challenge_id', '')
        sms.verify(cid, phone, 'register', data.get('code', ''))
        encoded = generate_password_hash(password)
        db, now, access = get_db(), int(time.time()), secrets.token_urlsafe(48)
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                sms.consume(db, cid, phone, 'register')
                uid = db.execute('INSERT INTO reader_users(nickname,nickname_key,password_hash,created_at,phone,phone_verified_at) VALUES(?,?,?,?,?,?)',
                    (nickname, nickname.casefold(), encoded, now_iso(), phone, now)).lastrowid
                for kind in ('terms', 'privacy'):
                    db.execute('INSERT INTO unified_consents(user_id,kind,version,accepted_at) VALUES(?,?,?,?)',
                        (uid, kind, POLICY_VERSION, now))
                row = db.execute('SELECT session_version FROM reader_users WHERE id=?', (uid,)).fetchone()
                db.execute('INSERT INTO unified_tokens VALUES(?,?,?,?,?,NULL)',
                    (digest(access), uid, row['session_version'], 'game', now + 43200))
        except sqlite3.IntegrityError:
            raise AccountError('用户名或手机号已被使用。已有账号请返回登录，或到 Orange 手记恢复账号。', 409)
        return {'ok': True, 'access_token': access, 'expires_in': 43200}

    @app.post('/internal/game/password/login')
    def unified_game_password_login():
        data = proof()
        from moderation import client_ip
        # Share the website's account/global budget; rotating game cookies cannot reset it.
        sms.reserve('game-password-ip', [('ip', client_ip(), 30, 900)])
        username, password = data.get('username'), data.get('password')
        if not isinstance(username, str) or len(username) > 256 or not isinstance(password, str) or not 1 <= len(password) <= 128:
            raise AccountError('用户名或密码不正确。', 401)
        nickname = unicodedata.normalize('NFKC', username).strip()
        if not 1 <= len(nickname) <= 24: raise AccountError('用户名或密码不正确。', 401)
        key = nickname.casefold()
        sms.reserve('reader-login', [('account', key, 30, 900),
            ('global', 'all', app.config['READER_LOGIN_GLOBAL_WINDOW'], 900)])
        db = get_db()
        row = db.execute('SELECT * FROM reader_users WHERE nickname_key=?', (key,)).fetchone()
        valid = check_password_hash(row['password_hash'] if row else dummy_hash, password)
        if not row or not valid or row['disabled_at'] or row['account_status'] != 'active':
            raise AccountError('用户名或密码不正确。', 401)
        if not complete(row):
            raise AccountError('请先到 Orange 手记用原用户名和密码登录，完成手机号绑定后再登录游戏。', 403)
        access, now = secrets.token_urlsafe(48), int(time.time())
        with db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM reader_users WHERE id=?', (row['id'],)).fetchone()
            if not complete(current) or current['session_version'] != row['session_version'] or current['password_hash'] != row['password_hash']:
                raise AccountError('账号状态已变化，请重新登录。', 401)
            for kind in ('terms', 'privacy'):
                db.execute('INSERT OR IGNORE INTO unified_consents(user_id,kind,version,accepted_at) VALUES(?,?,?,?)',
                    (row['id'], kind, POLICY_VERSION, now))
            db.execute('INSERT INTO unified_tokens VALUES(?,?,?,?,?,NULL)',
                (digest(access), row['id'], row['session_version'], 'game', now + 43200))
        return {'ok': True, 'access_token': access, 'expires_in': 43200}

    @app.post('/internal/game/phone/send')
    def unified_game_phone_send():
        data = proof()
        return sms.send(data.get('phone', ''), 'login')

    @app.post('/internal/game/phone/login')
    def unified_game_phone_login():
        data = proof()
        phone, cid = data.get('phone', ''), data.get('challenge_id', '')
        sms.verify(cid, phone, 'login', data.get('code', ''))
        db, now = get_db(), int(time.time())
        access = secrets.token_urlsafe(48)
        with db:
            db.execute('BEGIN IMMEDIATE')
            sms.consume(db, cid, phone, 'login')
            row = db.execute('SELECT * FROM reader_users WHERE phone=?', (phone,)).fetchone()
            created = row is None
            if row is None:
                nickname = '同学_' + secrets.token_hex(6)
                uid = db.execute('INSERT INTO reader_users(nickname,nickname_key,password_hash,created_at,phone,phone_verified_at) VALUES(?,?,?,?,?,?)',
                    (nickname, nickname.casefold(), PHONE_ONLY, now_iso(), phone, now)).lastrowid
                row = db.execute('SELECT * FROM reader_users WHERE id=?', (uid,)).fetchone()
            if not complete(row):
                raise AccountError('账号不可用或资料尚未补全，请到 Orange 手记处理后重试。', 403)
            for kind in ('terms', 'privacy'):
                db.execute('INSERT OR IGNORE INTO unified_consents(user_id,kind,version,accepted_at) VALUES(?,?,?,?)',
                    (row['id'], kind, POLICY_VERSION, now))
            db.execute('INSERT INTO unified_tokens VALUES(?,?,?,?,?,NULL)',
                (digest(access), row['id'], row['session_version'], 'game', now + 43200))
        # This token is returned only on the internal, client-authenticated channel.
        return {'ok': True, 'created': created, 'access_token': access, 'expires_in': 43200}
