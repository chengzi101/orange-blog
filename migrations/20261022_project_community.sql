-- Project interactions are independent of existing article interactions.
CREATE TABLE IF NOT EXISTS project_likes (
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES reader_users(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL,
 PRIMARY KEY(project_id,user_id)
);
CREATE TABLE IF NOT EXISTS project_comments (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES reader_users(id) ON DELETE CASCADE,
 content TEXT NOT NULL CHECK(length(content) BETWEEN 1 AND 2000),
 created_at TEXT NOT NULL,
 submission_id TEXT NOT NULL,
 parent_id INTEGER REFERENCES project_comments(id) ON DELETE SET NULL,
 reply_to_id INTEGER REFERENCES project_comments(id) ON DELETE SET NULL,
 deleted_at TEXT,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
 reviewed_at TEXT,
 reviewed_by TEXT,
 moderation_reason TEXT NOT NULL DEFAULT '',
 manual_approved INTEGER NOT NULL DEFAULT 0,
 content_fingerprint TEXT NOT NULL DEFAULT '',
 floor INTEGER,
 UNIQUE(user_id,submission_id)
);
CREATE TABLE IF NOT EXISTS project_comment_likes (
 comment_id INTEGER NOT NULL REFERENCES project_comments(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES reader_users(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL,
 PRIMARY KEY(comment_id,user_id)
);
CREATE INDEX IF NOT EXISTS idx_project_comments_time ON project_comments(project_id,created_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_project_comments_user ON project_comments(user_id,id DESC);
CREATE INDEX IF NOT EXISTS idx_project_comments_parent ON project_comments(project_id,parent_id,created_at,id);
CREATE INDEX IF NOT EXISTS idx_project_comments_moderation ON project_comments(status,created_at,id);
CREATE INDEX IF NOT EXISTS idx_project_comments_fingerprint ON project_comments(content_fingerprint,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_comment_floor ON project_comments(project_id,floor);
CREATE INDEX IF NOT EXISTS idx_project_likes_user ON project_likes(user_id,project_id);
CREATE INDEX IF NOT EXISTS idx_project_comment_likes_user ON project_comment_likes(user_id,comment_id);
