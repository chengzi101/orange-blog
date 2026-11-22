"""Additive migration. Never update existing identities or credentials."""
VERSION = '20261028_unified_accounts_v3_phone_login'
TABLES = (
'''CREATE TABLE IF NOT EXISTS unified_consents (
 user_id INTEGER NOT NULL REFERENCES reader_users(id), kind TEXT NOT NULL,
 version TEXT NOT NULL, accepted_at INTEGER NOT NULL,
 PRIMARY KEY(user_id,kind,version))''',
'''CREATE TABLE IF NOT EXISTS unified_migrations (version TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS unified_sms (
 id TEXT PRIMARY KEY, session_key TEXT NOT NULL, user_id INTEGER REFERENCES reader_users(id),
 phone TEXT NOT NULL, purpose TEXT NOT NULL, provider TEXT NOT NULL, mock_hash TEXT,
 created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 verified_at INTEGER, consumed_at INTEGER, ready INTEGER NOT NULL DEFAULT 0)''',
'''CREATE INDEX IF NOT EXISTS unified_sms_expiry ON unified_sms(expires_at)''',
'''CREATE TABLE IF NOT EXISTS unified_codes (
 code_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES reader_users(id),
 session_version INTEGER NOT NULL, redirect_uri TEXT NOT NULL, scope TEXT NOT NULL,
 code_challenge TEXT NOT NULL, code_challenge_method TEXT NOT NULL,
 expires_at INTEGER NOT NULL, consumed_at INTEGER)''',
'''CREATE TABLE IF NOT EXISTS unified_tokens (
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES reader_users(id),
 session_version INTEGER NOT NULL, scope TEXT NOT NULL,
 expires_at INTEGER NOT NULL, revoked_at INTEGER)''',
'''CREATE INDEX IF NOT EXISTS unified_tokens_user ON unified_tokens(user_id)''',
'''CREATE TABLE IF NOT EXISTS game_profiles (
 user_id INTEGER PRIMARY KEY REFERENCES reader_users(id),
 state_json TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS game_follows (
 user_id INTEGER NOT NULL REFERENCES reader_users(id), target_id INTEGER NOT NULL REFERENCES reader_users(id),
 created_at INTEGER NOT NULL, PRIMARY KEY(user_id,target_id), CHECK(user_id != target_id))''',
)

def migrate(db):
    import time
    cols = {r[1] for r in db.execute('PRAGMA table_info(reader_users)')}
    if not cols:
        raise RuntimeError('Existing reader_users table required')
    for name, sql in {
        'phone': 'TEXT', 'phone_verified_at': 'INTEGER',
        'phone_change_used': 'INTEGER NOT NULL DEFAULT 0 CHECK(phone_change_used IN (0,1))',
        'account_status': "TEXT NOT NULL DEFAULT 'active' CHECK(account_status IN ('active','locked'))",
    }.items():
        if name not in cols:
            db.execute('ALTER TABLE reader_users ADD COLUMN '+name+' '+sql)
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS unified_phone_unique ON reader_users(phone) WHERE phone IS NOT NULL')
    for sql in TABLES:
        db.execute(sql)
    db.execute('INSERT OR IGNORE INTO unified_migrations VALUES (?,?)', (VERSION,int(time.time())))

def complete(user):
    return bool(user and user['nickname'] and user['nickname_key'] and user['password_hash']
                and user['phone'] and user['phone_verified_at']
                and not user['disabled_at'] and user['account_status'] == 'active')
