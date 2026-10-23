"""Additive query indexes; no modification or deletion of business rows."""
def init_query_indexes(db):
    db.execute('CREATE INDEX IF NOT EXISTS idx_post_likes_user ON post_likes(user_id,post_id)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_comment_likes_user ON comment_likes(user_id,comment_id)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_posts_listing ON posts(status,is_featured DESC,published_at DESC,id DESC)')
