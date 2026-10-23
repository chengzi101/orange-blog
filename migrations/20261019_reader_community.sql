-- Additive, repeatable migration. Existing posts and views are preserved.
CREATE TABLE IF NOT EXISTS reader_users (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 nickname TEXT NOT NULL CHECK(length(nickname) BETWEEN 1 AND 24),
 nickname_key TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS post_likes (
 post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES reader_users(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL,
 PRIMARY KEY(post_id,user_id)
);
CREATE TABLE IF NOT EXISTS post_comments (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES reader_users(id) ON DELETE CASCADE,
 content TEXT NOT NULL CHECK(length(content) BETWEEN 1 AND 2000),
 created_at TEXT NOT NULL,
 submission_id TEXT NOT NULL,
 UNIQUE(user_id,submission_id)
);
CREATE INDEX IF NOT EXISTS idx_post_comments_time ON post_comments(post_id,created_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_post_comments_user ON post_comments(user_id,id DESC);
CREATE TABLE IF NOT EXISTS post_view_visitors (
 post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
 visitor_key TEXT NOT NULL,
 last_counted_at INTEGER NOT NULL,
 PRIMARY KEY(post_id,visitor_key)
);
CREATE TABLE IF NOT EXISTS reader_rate_limits (
 rate_key TEXT PRIMARY KEY,
 window_start INTEGER NOT NULL,
 attempts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS owner_login_tickets (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES reader_users(id) ON DELETE CASCADE, expires_at INTEGER NOT NULL);
