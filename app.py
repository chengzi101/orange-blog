from __future__ import annotations

import hmac
import hashlib
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from functools import wraps
from html import escape as xml_escape
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from security_helpers import constant_time_text_equal
from auth_security import (SQLiteSessionInterface, init_auth_schema, LoginLimited,
                           reserve_admin_login, consume_admin_ticket)
from public_limits import reserve_public
from runtime_security import runtime_security_config, TrustedHostMiddleware

import bleach
import markdown
from markdown.blockprocessors import ParagraphProcessor
from latex_renderer import LatexRenderError, latex_bundle
from community import init_community, install_community, snapshot_post_community, restore_post_community
from project_community import snapshot_project_community, restore_project_community
from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("ORANGE_DB", BASE_DIR / "data" / "blog.db"))
UPLOAD_DIR = Path(os.environ.get("ORANGE_UPLOADS", DB_PATH.parent / "uploads"))
ADMIN_USER = os.environ.get("ORANGE_ADMIN_USER", "orange")
ADMIN_PASSWORD_HASH = os.environ.get("ORANGE_ADMIN_PASSWORD_HASH", "")
ADMIN_AUTO_TOKEN = os.environ.get("ORANGE_ADMIN_AUTO_TOKEN", "")
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")

app = Flask(__name__)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
    AUTH_ABSOLUTE_SECONDS=12 * 60 * 60,
    ADMIN_IDLE_SECONDS=30 * 60,
    READER_IDLE_SECONDS=12 * 60 * 60,
    REGISTER_GLOBAL_HOUR=30,
    REGISTER_GLOBAL_DAY=100,
    READER_LOGIN_GLOBAL_WINDOW=120,
    LIKE_USER_MINUTE=60,
    LIKE_IP_MINUTE=180,
    LIKE_GLOBAL_MINUTE=600,
    READ_IP_MINUTE=120,
    READ_GLOBAL_MINUTE=600,
)
app.config.update(runtime_security_config(os.environ))
app.wsgi_app = TrustedHostMiddleware(app.wsgi_app, app.config['SECURITY_TRUSTED_HOSTS'])

ALLOWED_TAGS = {
    "p", "br", "strong", "em", "del", "blockquote", "code", "pre",
    "h1", "h2", "h3", "h4", "ul", "ol", "li", "a", "hr", "table",
    "thead", "tbody", "tr", "th", "td", "img",
}
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title"],
    "h1": ["id"], "h2": ["id"], "h3": ["id"], "h4": ["id"],
}

SETTING_DEFAULTS = {
    "hero_subtitle": "记录代码、模型，与生活现场。",
    "hero_kicker": "个人记录",
    "hero_title": "记录代码、模型",
    "hero_accent": "与生活现场。",
    "hero_intro": "你好，我是 Orange。这里存放我对技术、学习和日常的观察——写下已经想明白的，也保留仍在追问的。",
    "about_title": "保持好奇，持续构建。",
    "about_body": "我关注如何用清晰的方法处理复杂问题，也喜欢把一个想法真正做成可以使用的东西。这个网站是我的长期实验：少一点喧闹，多一点诚实、具体和可复用的经验。",
    "note_1": "简洁不是少写，而是只保留真正重要的部分。",
    "note_2": "正在阅读关于复杂系统与决策方法的书。",
    "note_3": "把重复的工作变成可以复用的流程。",
    "footer_quote": "写给未来的自己，也写给恰好路过的你。",
    "now_title": "此刻正在做什么",
    "now_body": "继续完善 Orange，把零散经验整理成文章、项目和可以复用的方法。",
    "skills": "Python,Flask,SQLite,数据分析,数学建模,数据库",
    "contact_email": "",
    "github_url": "",
}

ALLOWED_UPLOADS = {"png", "jpg", "jpeg", "gif", "webp"}

_schema_ready = False


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(DB_PATH, timeout=5)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA busy_timeout = 5000")
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


app.session_interface = SQLiteSessionInterface(get_db, lambda: (ADMIN_USER, ADMIN_PASSWORD_HASH))


@app.teardown_appcontext
def close_db(_: BaseException | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            excerpt TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '随笔',
            is_featured INTEGER NOT NULL DEFAULT 0,
            views INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'published')),
            published_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    init_community(db)
    init_auth_schema(db)
    existing_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(posts)").fetchall()
    }
    migrations = {
        "category": "ALTER TABLE posts ADD COLUMN category TEXT NOT NULL DEFAULT '随笔'",
        "is_featured": "ALTER TABLE posts ADD COLUMN is_featured INTEGER NOT NULL DEFAULT 0",
        "views": "ALTER TABLE posts ADD COLUMN views INTEGER NOT NULL DEFAULT 0",
        "published_at": "ALTER TABLE posts ADD COLUMN published_at TEXT NOT NULL DEFAULT ''",
        "content_format": "ALTER TABLE posts ADD COLUMN content_format TEXT NOT NULL DEFAULT 'markdown'",
    }
    for column, statement in migrations.items():
        if column not in existing_columns:
            try:
                db.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
        """
    )
    db.executemany(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        SETTING_DEFAULTS.items(),
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            mood TEXT NOT NULL DEFAULT '想法',
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'published')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            summary TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            stack TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'building' CHECK(status IN ('idea', 'building', 'shipped')),
            link TEXT NOT NULL DEFAULT '',
            year TEXT NOT NULL DEFAULT '',
            is_featured INTEGER NOT NULL DEFAULT 0,
            is_visible INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    if 'content_format' not in {row['name'] for row in db.execute('PRAGMA table_info(projects)')}:
        db.execute("ALTER TABLE projects ADD COLUMN content_format TEXT NOT NULL DEFAULT 'markdown'")
    db.execute(
        "UPDATE projects SET title = ?, updated_at = ? WHERE slug = ? AND title = ?",
        ("Orange手记", now_iso(), "orange-blog", "橙色手记"),
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_name TEXT NOT NULL,
            filename TEXT NOT NULL UNIQUE,
            mime_type TEXT NOT NULL DEFAULT '',
            size INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS post_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            excerpt TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
        )
        """
    )
    if 'content_format' not in {row['name'] for row in db.execute('PRAGMA table_info(post_revisions)')}:
        try:
            db.execute("ALTER TABLE post_revisions ADD COLUMN content_format TEXT NOT NULL DEFAULT 'markdown'")
        except sqlite3.OperationalError as error:
            if 'duplicate column name' not in str(error).lower():
                raise
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_stats (
            day TEXT NOT NULL,
            path TEXT NOT NULL,
            views INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(day, path)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS visitor_daily (
            day TEXT NOT NULL,
            visitor_hash TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            page_views INTEGER NOT NULL DEFAULT 1,
            first_path TEXT NOT NULL DEFAULT '/',
            last_path TEXT NOT NULL DEFAULT '/',
            referrer_host TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(day, visitor_hash)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS visit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visited_at TEXT NOT NULL,
            day TEXT NOT NULL,
            visitor_hash TEXT NOT NULL DEFAULT '',
            path TEXT NOT NULL DEFAULT '/',
            referrer_host TEXT NOT NULL DEFAULT '',
            user_agent TEXT NOT NULL DEFAULT '',
            browser TEXT NOT NULL DEFAULT '未知',
            operating_system TEXT NOT NULL DEFAULT '未知',
            device TEXT NOT NULL DEFAULT '未知设备',
            ip_masked TEXT NOT NULL DEFAULT '',
            is_bot INTEGER NOT NULL DEFAULT 0,
            bot_reason TEXT NOT NULL DEFAULT '',
            js_verified INTEGER NOT NULL DEFAULT 0,
            screen_size TEXT NOT NULL DEFAULT '',
            language TEXT NOT NULL DEFAULT '',
            timezone_name TEXT NOT NULL DEFAULT ''
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS trash_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type TEXT NOT NULL CHECK(item_type IN ('post', 'project', 'note', 'media')),
            original_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            payload TEXT NOT NULL,
            deleted_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            item_type TEXT NOT NULL DEFAULT '',
            item_title TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS posts_status_date_idx ON posts(status, published_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS posts_category_idx ON posts(category)")
    db.execute("CREATE INDEX IF NOT EXISTS visitor_daily_seen_idx ON visitor_daily(last_seen DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS visitor_daily_visitor_idx ON visitor_daily(visitor_hash, day)")
    db.execute("CREATE INDEX IF NOT EXISTS visit_events_time_idx ON visit_events(visited_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS visit_events_kind_idx ON visit_events(is_bot, js_verified, visited_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS visit_events_visitor_idx ON visit_events(visitor_hash, visited_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS trash_deleted_idx ON trash_items(deleted_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS activity_created_idx ON activity_log(created_at DESC)")

    base_seed_initialized = db.execute(
        "SELECT value FROM settings WHERE key = 'base_seed_initialized'"
    ).fetchone()
    has_existing_content = any(
        db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        for table in ("posts", "projects", "notes")
    )
    allow_base_seed = base_seed_initialized is None and not has_existing_content

    count = db.execute("SELECT COUNT(*) AS n FROM posts").fetchone()["n"]
    if count == 0 and allow_base_seed:
        seeds = [
            (
                "为什么我开始写这个博客", "why-i-started",
                "把零散的思考整理成可以再次抵达的地方，也记录那些真正解决了问题的方法。",
                """## 从这里开始\n\n写作不是为了制造更多信息，而是为了把真正有用的经验保存下来。\n\n这个网站会记录代码、模型与生活现场，也保留那些仍在追问的问题。""",
                "随笔,开始", "随笔", 1,
            ),
            (
                "从数据到决策：一次建模工作的完整路径", "data-to-decision",
                "比答案更重要的是证据链：怎样检查数据、选择路线，并让每一步都能够复现。",
                """## 先建立证据链\n\n一项可靠的建模工作通常从数据审计开始，而不是从复杂算法开始。\n\n1. 检查数据来源和口径\n2. 建立可复现的基线\n3. 再逐步增加模型复杂度\n4. 用结果验证每个假设""",
                "数学建模,方法", "研究", 0,
            ),
            (
                "并发系统里，那些不能靠运气的细节", "concurrency-details",
                "从事务、锁到压力测试，聊聊一个可靠系统如何应对同一时刻发生的事情。",
                """## 可靠性不是偶然\n\n并发系统需要明确事务边界、锁的粒度和失败后的恢复路径。只有经过压力测试，设计中的假设才会真正暴露出来。""",
                "数据库,工程", "技术", 0,
            ),
        ]
        created = now_iso()
        db.executemany(
            """
            INSERT INTO posts
            (title, slug, excerpt, content, tags, category, is_featured, views,
             status, published_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'published', ?, ?, ?)
            """,
            [(*seed, created, created, created) for seed in seeds],
        )

    db.execute(
        "UPDATE posts SET published_at = created_at WHERE status = 'published' AND published_at = ''"
    )
    featured_count = db.execute(
        "SELECT COUNT(*) AS n FROM posts WHERE status = 'published' AND is_featured = 1"
    ).fetchone()["n"]
    if featured_count == 0 and allow_base_seed:
        first = db.execute(
            "SELECT id FROM posts WHERE status = 'published' ORDER BY published_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if first:
            db.execute("UPDATE posts SET is_featured = 1 WHERE id = ?", (first["id"],))

    created = now_iso()
    if (
        db.execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"] == 0
        and allow_base_seed
    ):
        settings = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings")}
        note_seeds = [
            (settings.get("note_1", SETTING_DEFAULTS["note_1"]), "想法"),
            (settings.get("note_2", SETTING_DEFAULTS["note_2"]), "阅读"),
            (settings.get("note_3", SETTING_DEFAULTS["note_3"]), "方法"),
            ("真正有效的工具，应该让复杂的事情变得更容易解释。", "记录"),
            ("先把最小版本做出来，再让每一次修改都有清楚的理由。", "构建"),
        ]
        db.executemany(
            "INSERT INTO notes (content, mood, status, created_at, updated_at) VALUES (?, ?, 'published', ?, ?)",
            [(content, mood, created, created) for content, mood in note_seeds],
        )
    if (
        db.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"] == 0
        and allow_base_seed
    ):
        project_seeds = [
            (
                "Orange手记", "orange-blog",
                "一个可以持续写作、整理项目和保存片段的个人内容空间。",
                "## 为什么做它\n\n我想要一个真正属于自己的表达空间：能写长文，也能保存短暂想法，还能展示正在构建的项目。",
                "Flask,SQLite,Nginx", "shipped", "", "2026", 1, 1, 30,
            ),
            (
                "数据到决策工作流", "data-decision-workflow",
                "从数据审计、基线验证到模型解释，把一次分析变成可复现的过程。",
                "## 核心原则\n\n先建立证据链，再增加模型复杂度；每一步都保留检查结果和决策理由。",
                "Python,数据分析,数学建模", "building", "", "2026", 1, 1, 20,
            ),
            (
                "并发系统实验", "concurrency-lab",
                "围绕事务、锁和压力测试，记录可靠系统不能依赖运气的那些细节。",
                "## 关注的问题\n\n并发写入、事务边界、失败恢复，以及如何用压力测试验证设计假设。",
                "数据库,并发,测试", "building", "", "2026", 0, 1, 10,
            ),
        ]
        db.executemany(
            """
            INSERT INTO projects
            (title, slug, summary, content, stack, status, link, year, is_featured,
             is_visible, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(*seed, created, created) for seed in project_seeds],
        )
    db.execute(
        """
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('base_seed_initialized', 'applied')
        """
    )

    content_pack = db.execute(
        "SELECT value FROM settings WHERE key = 'content_pack_rich_v1'"
    ).fetchone()
    if content_pack is None:
        rich_posts = [
            (
                "从静态页面到长期写作系统：Orange 的三次重做",
                "orange-from-page-to-system",
                "一个个人网站真正困难的部分，不是把首页做出来，而是让它在半年后仍然愿意被打开、更新和继续使用。",
                """## 第一版：先让它出现

最早的 Orange 更接近一张精心排版的名片。它有醒目的标题、黑与橙的视觉和几篇预先写好的内容，但页面里的文字都固定在代码里。想更新一篇文章，就要重新修改文件、上传并发布。

它看起来像一个博客，却还不是一个可以长期写作的系统。

## 第二版：把内容从页面里拿出来

真正的转折，是加入独立的管理后台和数据库。文章、项目、片段、网站介绍不再和页面模板绑在一起，可以在浏览器里创建、预览、保存草稿和发布。

这一步让我重新理解了「功能完整」：不是页面上出现了多少按钮，而是最常用的路径是否足够短。

- 写一个念头，用片段；
- 解释完整过程，用文章；
- 记录持续构建的东西，用项目；
- 不确定时先存草稿，而不是被迫一次写完。

## 第三版：让系统帮助内容生长

只有编辑器仍然不够。一个长期写作空间，还需要搜索、主题索引、相关阅读、历史版本、媒体库和清楚的内容状态。它们不直接产生文字，却能降低整理和再次发现内容的成本。

这次重做也刻意减少了没有目的的动画。保存、复制、发布需要明确反馈；频繁编辑不需要漫长过场。视觉应该帮助理解层级，而不是抢走注意力。

## 我现在怎样看个人网站

个人网站不是一次完成的作品，而是一套会暴露真实习惯的工具。你愿不愿意持续使用它，比第一次打开时有多惊艳更重要。

Orange 还会继续变化，但判断标准会保持简单：它是否让我更容易写下、整理和重新找到真正重要的东西。""",
                "建站,产品,Flask,写作系统",
                "技术",
            ),
            (
                "我为什么把博客后台放在一条 SSH 隧道后面",
                "private-admin-over-ssh",
                "公开阅读和私人编辑是两条完全不同的路径。后台不必为了方便访问，就同时暴露给整个互联网。",
                """## 两个入口，两种风险

博客前台的目标是尽可能容易访问；后台的目标则是只让一个确定的人进入。把两者放在同一套公开入口下虽然省事，却意味着登录页、会话和管理接口都会长期面对扫描与尝试。

Orange 采用了更直接的方式：公开网站只开放阅读页面，管理路径在公网返回不存在。需要写作时，桌面启动器先建立一条 SSH 端口转发，再从本机打开后台。

## 这条链路在做什么

访问过程可以拆成四步：

1. 桌面启动器读取本机私钥；
2. SSH 连接服务器并建立本地端口转发；
3. 浏览器打开 `127.0.0.1` 上的后台地址；
4. 一次性快速登录令牌建立管理会话。

服务器上的后台服务仍只监听本机地址，Nginx 对公网隐藏管理路径。公开读者和私人编辑走的是两条不同的通道。

## 安全不是堆更多登录框

这套方案没有消灭所有风险，但减少了最常见的暴露面：攻击者甚至无法从公网触达后台登录页。真正承担身份验证的是 SSH 密钥，博客自己的账号密码成为第二层保护。

当然，它也带来代价：换电脑时需要重新安全地配置密钥，隧道异常时要能给出清楚提示，私钥权限不能过宽。

## 我更在意默认状态

可靠系统往往依赖「默认就安全」，而不是要求每次操作都格外小心。公开入口默认看不到后台；启动器默认检查连接；部署默认保留数据库；这些小约束组合起来，比一条写在文档里的提醒更可靠。""",
                "SSH,安全,Nginx,部署",
                "技术",
            ),
            (
                "一个交互值不值得做，我会先问这四个问题",
                "should-this-interaction-animate",
                "动效不是页面变高级的捷径。判断一个交互是否值得做，要先看频率、目的、速度和是否真的改善了使用。",
                """## 一、这个动作一天会发生多少次

越高频的动作，越应该安静。编辑文字、切换字段、使用快捷键保存，用户的注意力已经在任务本身，不需要每次都看一段表演。

偶尔发生的状态变化——例如发布成功、复制完成、第一次进入页面——才适合加入短暂反馈。

## 二、它解释了什么

好的动效至少完成一件事：

- 告诉我操作已经发生；
- 说明一个元素从哪里来、到哪里去；
- 帮助理解状态发生了变化；
- 避免内容突然跳动。

如果删掉动效之后，信息没有任何损失，它大概率只是装饰。

## 三、它能不能足够快

界面反馈应该先于犹豫。按钮按下可以在一百多毫秒内轻微缩放，菜单和提示通常也不需要超过三百毫秒。进入使用 `ease-out`，移动使用 `ease-in-out`，让动作一开始就响应，而不是先慢慢蓄力。

## 四、它会不会妨碍下一次操作

交互必须可以被打断。用户快速点击、返回或继续输入时，上一段效果不应该把界面锁住。性能上优先改变 `transform` 和 `opacity`，并尊重系统的「减少动态效果」设置。

## 最后才是好不好看

视觉风格当然重要，但它应该排在功能之后。Orange 的按钮按压、卡片层次和保存状态都很轻，因为它们的任务是确认、区分和引导，而不是证明页面会动。

我越来越相信，成熟的交互设计不是不断增加效果，而是知道哪些瞬间值得被认真处理。""",
                "设计,交互,动效,体验",
                "设计",
            ),
        ]
        db.executemany(
            """
            INSERT OR IGNORE INTO posts
            (title, slug, excerpt, content, tags, category, is_featured, views,
             status, published_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, 'published', ?, ?, ?)
            """,
            [(*item, created, created, created) for item in rich_posts],
        )
        rich_notes = [
            ("个人网站不是一次完成的作品，而是一套会暴露真实习惯的工具。", "建站"),
            ("高频操作应该安静；偶尔发生的重要变化，才值得被认真强调。", "设计"),
            ("公开阅读和私人编辑是两条路径，不必为了方便把它们同时暴露。", "安全"),
            ("一个内容系统最有价值的能力，是让旧内容能够被重新发现。", "写作"),
            ("先记录问题发生的现场，再总结可以复用的方法。", "方法"),
            ("好的默认值，是替未来的自己少做一次决定。", "产品"),
        ]
        db.executemany(
            "INSERT INTO notes (content, mood, status, created_at, updated_at) VALUES (?, ?, 'published', ?, ?)",
            [(content, mood, created, created) for content, mood in rich_notes],
        )
        db.execute(
            """
            UPDATE projects
            SET summary = ?,
                content = ?,
                updated_at = ?
            WHERE slug = 'orange-blog'
            """,
            (
                "从静态首页成长为拥有文章、项目、片段、媒体、版本历史和私有后台的长期写作系统。",
                """## 项目目标

Orange 不是一次性展示页，而是一个可以持续写作和整理的个人内容空间。

## 已完成

- 文章、项目与片段的独立管理
- Markdown 编辑、预览和历史版本
- 搜索、分类、标签、相关阅读与 RSS
- 媒体上传和内容统计
- SSH 隧道保护的私人后台
- 面向手机与桌面的响应式阅读体验

## 仍在继续

内容系统永远不会真正完成。接下来会继续改善主题发现、内容之间的关联，以及写作时的低摩擦体验。""",
                created,
            ),
        )
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('content_pack_rich_v1', 'applied')"
        )

    content_pack_v2 = db.execute(
        "SELECT value FROM settings WHERE key = 'content_pack_rich_v2'"
    ).fetchone()
    if content_pack_v2 is None:
        method_posts = [
            (
                "50 个并发请求之后：如何证明系统没有超卖",
                "proving-no-oversell-under-concurrency",
                "并发测试不是为了让终端滚过一排绿色文字，而是把“不会超卖”写成可以被验证、被重复、也可能被推翻的条件。",
                """## 一个更具体的问题

“系统支持并发”这句话太宽泛，几乎无法验证。我把问题缩小成一个明确场景：50 个用户同时购买同一车次、同一席别，而库存只有 20。

如果系统正确，结果不只是成功请求数量等于 20。它还应该同时满足：

- 20 个请求成功，30 个请求明确失败；
- 可售库存始终不小于 0；
- 没有同一座位对应两张有效车票；
- `Available + Locked + Sold = Total` 在测试前后都成立。

这些条件比“接口没有报错”严格得多，因为它们直接描述了业务世界不能被破坏的规则。

## 把竞争放进同一时间窗口

测试使用 Python 的 `ThreadPoolExecutor` 组织 50 个线程，通过 `pyodbc` 同时发起购买。重点不是追求极端吞吐量，而是尽可能制造对同一份库存的竞争。

如果所有请求依次执行，即使事务设计有缺陷，也可能看起来完全正常。只有让读取库存、判断可售和扣减库存发生重叠，隐藏的竞态条件才更容易暴露。

## 事务里需要重新确认

购买前的页面检查只能改善体验，不能成为最终依据。真正的库存确认必须发生在订单事务内部：

1. 确认车次仍处于可售状态；
2. 检查起点到终点的每一个区间都有余量；
3. 获取车次、席别和区间对应的排他应用锁；
4. 使用更新锁保护库存行；
5. 完成统一扣减后再提交事务。

事务外看到的“还有一张”，在事务真正执行时可能已经不存在。可靠系统必须接受这个事实。

## 锁不是唯一答案

应用锁和行锁负责协调正在发生的竞争，唯一占用约束则是最后一道防线。即使上层逻辑遗漏了某条路径，数据库仍应拒绝重复有效票。

我更喜欢把这类设计理解成多层证据：

- 业务条件说明什么算正确；
- 事务和锁尽量保证正确；
- 唯一约束阻止最坏结果；
- 压力测试证明这些机制在竞争下仍然有效。

## 失败请求也需要被设计

库存只有 20 时，30 个失败请求不是异常，而是预期结果。真正的问题是：失败是否清楚、是否回滚完整、是否留下半完成订单，以及用户能否理解下一步。

并发正确性不等于所有人都成功。它意味着在不可能全部成功时，系统仍然以可解释、可恢复的方式拒绝一部分请求。""",
                "数据库,并发,事务,测试",
                "工程",
            ),
            (
                "建模之前，我先花时间证明数据能不能用",
                "audit-data-before-modeling",
                "模型会忠实地放大输入里的假设。与其先寻找复杂算法，不如先把重复、冲突、异常和未知组合说清楚。",
                """## 数据文件不是天然的事实

在一次排产数据处理中，历史记录最初有 5,348 行。第一轮检查就发现了 201 行完全重复记录，去重后剩下 5,147 行。

如果跳过这一步，后续统计会把重复记录当成重复生产，产量、班次频次和产品兼容性都会被悄悄放大。

## 清洗不等于尽可能删除

我把问题分成了几种不同类型：

- 完全重复：可以删除，并记录删除数量；
- 关键字段无效或产量非正：无法参与计算，需要排除；
- 班次缺失：填为“未知”，保留这条生产事实；
- 同一日期、产线、班次出现冲突：显式标记；
- 组内 IQR 异常：标记但不直接删除。

最终保留的历史数据中，标记了 188 个冲突班次槽位和 64 行组内异常。它们不是自动等同于错误，而是提醒后续模型不要把所有观测都当作同等可靠。

## 历史最大值不是物理产能

历史记录只能说明“过去曾经这样安排”，不能直接证明设备的硬上限。把历史最大计划量当作物理产能，会把临时订单、缺料、检修和人为排程策略一起误写进约束。

更稳妥的方法是先生成产品、酒质、产线的兼容与容量特征，再区分：

- 来自题目或设备说明的硬约束；
- 从历史数据估计的经验参数；
- 仍需通过业务知识确认的假设。

## 未出现过的组合尤其重要

周需求中的产品组合都能在历史记录里找到，但月需求出现了一个历史未见组合 `P0002-Q04`。

它不能简单地继承一个不存在的平均速度，也不应该被模型静默删除。后续排程必须为它设置冷启动规则、替代产线逻辑或缺口惩罚。

## 质量报告是模型的一部分

清洗后的 CSV 只是交付物之一。我还会同时保存：

- 数据质量统计；
- 冲突与异常标记；
- 清洗规则和删除原因；
- 分组统计与容量特征；
- 后续建模仍需处理的未知组合。

这些记录让“数据可用”不再是一句判断，而是一条可以追溯的证据链。模型从这里开始，而不是从求解器开始。""",
                "数学建模,数据审计,预处理,证据链",
                "研究",
            ),
            (
                "把中文数据交付做对：内部字段、导出表头与 Excel 编码",
                "reliable-chinese-data-delivery",
                "代码里的字段名适合计算，不等于交付给人阅读的表头。一个小小的导出层，可以同时保留实现稳定性和中文可读性。",
                """## 计算逻辑和展示语言不是一回事

数据处理代码经常使用简短、稳定的内部字段名，它们便于引用、分组和复用。但最终 CSV 需要直接进入 Excel、报告或论文时，读者更关心的是字段能不能立刻看懂。

最容易走偏的做法，是为了中文表头重写整个处理流程。这样会扩大改动范围，也容易破坏已经验证过的计算逻辑。

我更倾向于保留两层：

1. 内部层继续使用稳定的处理字段；
2. 导出前统一映射为中文展示表头。

例如，内部计算可以保持一致的列引用，最终再映射为“产品、酒质、产线、生产日期、班次、计划生产量”等面向交付的名称。

## 为什么要在导出时转换

把转换集中在导出层有几个好处：

- 清洗、分组和特征提取逻辑不需要跟着展示语言变化；
- 同一份内部结果可以导出中文、英文或不同报告版本；
- 测试可以分别检查数值正确性和表头正确性；
- 修改列名时，不会意外影响模型输入。

这是一种很小的边界，却能减少很多不必要的重构。

## UTF-8 还不一定够

CSV 使用 UTF-8 编码，在程序之间通常没有问题；但直接双击用部分版本的 Excel 打开时，中文仍可能显示为乱码。

面向人工交付时，我会使用带 BOM 的 UTF-8，并实际重新打开或重新读取文件验证：

- 中文表头是否完整；
- 行列数量是否保持不变；
- 数值列有没有被错误转换；
- 文件能否被 Excel 直接识别。

编码不是文件写出来之后的附属问题。只要交付对象是人，打开方式就是结果的一部分。

## 好的交付减少解释成本

一个数据产物不仅要“能被代码读取”，还应该让下一位使用者知道它是什么、从哪里来、做过哪些处理、还有哪些限制。

因此我会把 CSV、质量报告和处理说明一起交付。字段语言、编码、文件命名和验证记录看起来都是小事，但它们共同决定了结果能不能被放心地继续使用。""",
                "数据处理,中文表头,Excel,工程",
                "方法",
            ),
        ]
        db.executemany(
            """
            INSERT OR IGNORE INTO posts
            (title, slug, excerpt, content, tags, category, is_featured, views,
             status, published_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, 'published', ?, ?, ?)
            """,
            [(*item, created, created, created) for item in method_posts],
        )
        method_notes = [
            ("并发测试首先要定义不变量：什么结果一旦出现，就证明系统已经错了。", "数据库"),
            ("失败请求不是噪声。资源有限时，清楚而完整地失败也是正确性的一部分。", "工程"),
            ("历史最大值描述过去，不自动等于设备的物理上限。", "建模"),
            ("异常值先标记再判断，比不加解释地删除更诚实。", "数据"),
            ("一个从未出现过的组合，往往比一千条正常记录更值得先处理。", "观察"),
            ("内部字段服务计算，导出表头服务阅读，两者不必强行共用一种语言。", "交付"),
            ("文件能被程序读取，不代表它已经适合交付给人。", "方法"),
            ("把检查结果写下来，是为了让下一次判断不必重新依赖记忆。", "记录"),
        ]
        db.executemany(
            "INSERT INTO notes (content, mood, status, created_at, updated_at) VALUES (?, ?, 'published', ?, ?)",
            [(content, mood, created, created) for content, mood in method_notes],
        )
        method_projects = [
            (
                "售票系统数据库",
                "ticketing-database-system",
                "围绕区间库存、订单事务和并发购票构建的数据库系统，并用压力测试验证不超卖与库存守恒。",
                """## 关注的问题

同一车次的座位会跨越多个站点区间被占用，库存判断不能只看起点和终点。系统需要在订单事务内重新检查所有相关区间，并处理多个用户同时购买最后几张票的竞争。

## 可靠性设计

- 车次、席别和区间级应用锁
- 库存行更新锁与事务隔离
- 唯一占用约束作为数据库防线
- 预订、支付、取消和退款的事务化存储过程

## 验证

50 个并发用户竞争 20 份库存时，预期 20 个成功、30 个失败，同时验证库存非负、库存守恒和不存在重复有效票。""",
                "SQL Server,Python,pyodbc,并发测试",
                "shipped",
                "",
                "2026",
                0,
                1,
                18,
            ),
            (
                "酒厂排产数据工作流",
                "brewery-scheduling-data-workflow",
                "把历史生产、周需求和月需求整理成可复现的数据审计、特征提取与后续排产输入。",
                """## 从数据检查开始

先核对题面、附件结构与字段口径，再处理重复、缺失班次、冲突槽位和组内异常。异常记录保留标记，避免在没有业务依据时直接删除。

## 产物

- 清洗后的历史生产记录
- 周需求与月需求标准化结果
- 班次统计和容量特征
- 机器可读的质量报告
- 面向阅读的中文说明

## 后续路线

数据层完成后，再依次进入容量与兼容矩阵、基线排产、多目标优化和不确定场景下的动态调整。""",
                "Python,数据审计,数学建模,排产",
                "building",
                "",
                "2026",
                1,
                1,
                16,
            ),
        ]
        db.executemany(
            """
            INSERT OR IGNORE INTO projects
            (title, slug, summary, content, stack, status, link, year, is_featured,
             is_visible, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(*item, created, created) for item in method_projects],
        )
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('content_pack_rich_v2', 'applied')"
        )
    db.commit()


@app.before_request
def ensure_schema() -> None:
    global _schema_ready
    if getattr(g, 'auth_storage_unavailable', False):
        abort(503)
    # Flask's int converter has no SQLite INTEGER upper bound.
    if any(isinstance(value, int) and not 0 <= value <= 2**63 - 1
           for value in (request.view_args or {}).values()):
        abort(404)
    if not _schema_ready:
        init_db()
        _schema_ready = True


@app.before_request
def public_read_budget():
    endpoints={'home','articles','post_detail','archive','projects','project_detail','garden',
               'uses','colophon','about','toolbox','toolbox_download','theme_taxonomy','theme_tag','theme_category',
               'theme_archives','theme_search_index','feed','sitemap','reader.comments','reader.comment_replies',
               'reader.project_comments','reader.project_comment_replies','reader.reader_session','reader.login','reader.register','profile.account','profile.avatar'}
    if request.method not in {'GET','HEAD'} or request.endpoint not in endpoints:
        return
    from moderation import client_ip
    try:
        reserve_public(app,get_db(),'read',[
            ('ip',client_ip(),app.config['READ_IP_MINUTE'],60),
            ('global','all',app.config['READ_GLOBAL_MINUTE'],60)])
    except LoginLimited as error:
        response=app.make_response(({'ok':False,'message':'访问较频繁，请稍后重试。'},429))
        response.headers['Retry-After']=str(error.retry_after)
        return response
    except sqlite3.DatabaseError:
        return {'ok':False,'message':'服务暂时繁忙，请稍后重试。'},503


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token


def validate_csrf() -> None:
    expected = session.get("csrf_token", "")
    received = request.form.get("csrf_token", "")
    if not expected or not constant_time_text_equal(expected, received):
        abort(400, "表单已失效，请刷新页面后重试。")


def selected_content_ids(limit: int = 500) -> list[int]:
    selected: list[int] = []
    for value in request.form.getlist("selected_ids"):
        try:
            item_id = int(value)
        except (TypeError, ValueError):
            continue
        if 0 < item_id <= 2**63 - 1 and item_id not in selected:
            selected.append(item_id)
        if len(selected) > limit:
            abort(400, "一次选择的内容过多，请分批操作。")
    return selected


TRASH_TYPES = {
    "post": {
        "table": "posts",
        "title": "title",
        "columns": [
            "id", "title", "slug", "excerpt", "content", "tags", "category",
            "is_featured", "views", "status", "published_at", "created_at", "updated_at", "content_format",
        ],
    },
    "project": {
        "table": "projects",
        "title": "title",
        "columns": [
            "id", "title", "slug", "summary", "content", "stack", "status", "link",
            "year", "is_featured", "is_visible", "sort_order", "created_at", "updated_at",
        ],
    },
    "note": {
        "table": "notes",
        "title": "content",
        "columns": ["id", "content", "mood", "status", "created_at", "updated_at"],
    },
    "media": {
        "table": "media",
        "title": "original_name",
        "columns": ["id", "original_name", "filename", "mime_type", "size", "created_at"],
    },
}


def log_admin_action(
    action: str, item_type: str = "", item_title: str = "", detail: str = ""
) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO activity_log (action, item_type, item_title, detail, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (action, item_type, item_title[:180], detail[:300], now_iso()),
    )
    db.execute(
        """
        DELETE FROM activity_log WHERE id NOT IN (
            SELECT id FROM activity_log ORDER BY id DESC LIMIT 300
        )
        """
    )


def move_content_to_trash(item_type: str, selected: list[int]) -> int:
    config = TRASH_TYPES[item_type]
    table = config["table"]
    placeholders = ",".join("?" for _ in selected)
    db = get_db()
    rows = db.execute(
        f"SELECT * FROM {table} WHERE id IN ({placeholders})", selected
    ).fetchall()
    deleted_at = now_iso()
    for row in rows:
        payload = dict(row)
        if item_type == "post":
            payload["__community"] = snapshot_post_community(db, row["id"])
            payload["__revisions"] = [
                dict(item)
                for item in db.execute(
                    "SELECT * FROM post_revisions WHERE post_id = ? ORDER BY id",
                    (row["id"],),
                ).fetchall()
            ]
        if item_type == "project":
            payload["__community"] = snapshot_project_community(db, row["id"])
        title = str(row[config["title"]])
        db.execute(
            """
            INSERT INTO trash_items (item_type, original_id, title, payload, deleted_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                item_type,
                row["id"],
                title[:180],
                json.dumps(payload, ensure_ascii=False),
                deleted_at,
            ),
        )
        log_admin_action("移入回收站", item_type, title)
    if rows:
        db.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", selected)
    db.commit()
    return len(rows)


def delete_trashed_media_file(payload: dict) -> bool:
    filename = str(payload.get("filename", ""))
    target = (UPLOAD_DIR / filename).resolve()
    if not filename or target.parent != UPLOAD_DIR.resolve() or not target.exists():
        return True
    try:
        target.unlink()
        return True
    except OSError:
        return False



TRASH_RETENTION_DAYS = 30


@app.template_filter("trash_expiry")
def trash_expiry(value: str) -> str:
    try:
        deleted = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (deleted + timedelta(days=TRASH_RETENTION_DAYS)).isoformat()
    except (ValueError, TypeError):
        return ""


def purge_expired_trash(now: datetime | None = None, dry_run: bool = False) -> dict[str, int]:
    """Purge only expired trash; serialize against restores and retain failed files."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = (current.astimezone(timezone.utc) - timedelta(days=TRASH_RETENTION_DAYS)).isoformat()
    db = get_db()
    result = {"eligible": 0, "deleted": 0, "failed": 0, "retained_files": 0}
    db.execute("BEGIN IMMEDIATE")
    try:
        expired = db.execute(
            "SELECT * FROM trash_items WHERE julianday(deleted_at) <= julianday(?) ORDER BY id",
            (cutoff,),
        ).fetchall()
        result["eligible"] = len(expired)
        if dry_run:
            db.rollback()
            return result
        # A shared or restored media file must survive another trash entry expiring.
        active_files = {row["filename"] for row in db.execute("SELECT filename FROM media")}
        media_refs: dict[str, int] = {}
        for row in db.execute("SELECT payload FROM trash_items WHERE item_type = 'media'"):
            try:
                data = json.loads(row["payload"])
                filename = data.get("filename", "") if isinstance(data, dict) else ""
                if filename:
                    media_refs[filename] = media_refs.get(filename, 0) + 1
            except (ValueError, TypeError):
                continue
        for item in expired:
            filename = ""
            if item["item_type"] == "media":
                try:
                    payload = json.loads(item["payload"])
                    if not isinstance(payload, dict) or not isinstance(payload.get("filename"), str) or not payload["filename"]:
                        raise ValueError("Invalid media payload")
                    filename = payload["filename"]
                except (ValueError, TypeError):
                    result["failed"] += 1
                    continue
                if filename in active_files or media_refs.get(filename, 0) > 1:
                    result["retained_files"] += 1
                elif not delete_trashed_media_file(payload):
                    result["failed"] += 1
                    continue
            db.execute("DELETE FROM trash_items WHERE id = ?", (item["id"],))
            if filename:
                media_refs[filename] = max(0, media_refs.get(filename, 0) - 1)
            result["deleted"] += 1
        if result["deleted"]:
            log_admin_action("自动清理回收站", "system", "", f"保留满 {TRASH_RETENTION_DAYS} 天，彻底删除 {result['deleted']} 项")
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_authenticated"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def normalize_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or f"post-{datetime.now().strftime('%Y%m%d%H%M%S')}"


def split_tags(value: str) -> list[str]:
    return [tag.strip() for tag in re.split(r"[,，]", value or "") if tag.strip()]


def plain_excerpt(content: str, limit: int = 120) -> str:
    content = re.sub(r'(?<=\))\{width="(?:[1-9]\d|100)%"\}', "", content or "")
    text = re.sub(r"[#>*_`\[\]()!-]", " ", content or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def reading_minutes(content: str) -> int:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", content or ""))
    words = len(re.findall(r"[A-Za-z0-9]+", content or ""))
    return max(1, round((chinese + words * 1.8) / 300))


class PreserveParagraphIndent(ParagraphProcessor):
    """Retain prose indentation after Markdown has handled structural blocks."""

    def run(self, parent, blocks):
        indent = re.match(r"^[^\S\r\n]+(?=\S)", blocks[0])
        child_count = len(parent)
        super().run(parent, blocks)
        if indent and len(parent) > child_count and parent[-1].tag == 'p':
            paragraph = parent[-1]
            paragraph.text = indent.group(0) + (paragraph.text or '')


def markdown_attribute_allowed(tag: str, name: str, value: str) -> bool:
    if tag == "img" and name == "width":
        return bool(re.fullmatch(r"(?:[1-9]\d|100)%", value))
    return name in ALLOWED_ATTRIBUTES.get(tag, [])


def markdown_bundle(value: str) -> tuple[str, str]:
    renderer = markdown.Markdown(
        extensions=["extra", "sane_lists", "tables", "toc"],
        extension_configs={"toc": {"permalink": False}},
        output_format="html5",
    )
    # Prose indentation is literal; code blocks require explicit fences.
    # Keep list indentation processors, so nested lists retain their structure.
    renderer.parser.blockprocessors.deregister("code")
    renderer.parser.blockprocessors.register(PreserveParagraphIndent(renderer.parser), "paragraph", 10)
    raw = renderer.convert(value or "")
    cleaned = bleach.clean(
        raw,
        tags=ALLOWED_TAGS,
        attributes=markdown_attribute_allowed,
        protocols={"http", "https", "mailto"},
        strip=True,
    )
    toc = bleach.clean(
        renderer.toc,
        tags={"div", "ul", "li", "a"},
        attributes={"div": ["class"], "a": ["href"]},
        strip=True,
    )
    return cleaned, toc


@app.template_filter("markdown")
def markdown_filter(value: str) -> str:
    return markdown_bundle(value)[0]


def article_bundle(value: str, content_format: str = 'markdown') -> tuple[str, str]:
    return latex_bundle(value) if content_format == 'latex' else markdown_bundle(value)


@app.template_filter("date_zh")
def date_zh(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo:
            parsed = parsed.astimezone(SHANGHAI)
        return parsed.strftime("%Y.%m.%d")
    except (ValueError, TypeError):
        return (value or "")[:10]


@app.template_filter("date_time_zh")
def date_time_zh(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo:
            parsed = parsed.astimezone(SHANGHAI)
        return parsed.strftime("%Y.%m.%d %H:%M")
    except (ValueError, TypeError):
        return (value or "")[:16]


@app.template_filter("tags_list")
def tags_list(value: str) -> list[str]:
    return split_tags(value)


@app.template_filter("reading_time")
def reading_time(value: str) -> int:
    return reading_minutes(value)


def get_settings() -> dict[str, str]:
    values = dict(SETTING_DEFAULTS)
    values.update({row["key"]: row["value"] for row in get_db().execute("SELECT key, value FROM settings")})
    return values


def get_recent_activity(limit: int = 8) -> list[dict[str, str]]:
    db = get_db()
    activity: list[dict[str, str]] = []
    for item in db.execute(
        """
        SELECT title, slug, category, updated_at FROM posts
        WHERE status = 'published' ORDER BY updated_at DESC LIMIT 8
        """
    ).fetchall():
        activity.append(
            {
                "kind": "文章",
                "title": item["title"],
                "meta": item["category"],
                "updated_at": item["updated_at"],
                "url": url_for("post_detail", slug=item["slug"]),
            }
        )
    for item in db.execute(
        """
        SELECT title, slug, status, updated_at FROM projects
        WHERE is_visible = 1 ORDER BY updated_at DESC LIMIT 6
        """
    ).fetchall():
        activity.append(
            {
                "kind": "项目",
                "title": item["title"],
                "meta": project_status(item["status"]),
                "updated_at": item["updated_at"],
                "url": url_for("project_detail", slug=item["slug"]),
            }
        )
    for item in db.execute(
        """
        SELECT id, content, mood, updated_at FROM notes
        WHERE status = 'published' ORDER BY updated_at DESC LIMIT 8
        """
    ).fetchall():
        activity.append(
            {
                "kind": "片段",
                "title": item["content"],
                "meta": item["mood"],
                "updated_at": item["updated_at"],
                "url": url_for("notes") + f"#note-{item['id']}",
            }
        )
    return sorted(
        [item for item in activity if item["kind"] != "片段"], key=lambda item: item["updated_at"], reverse=True
    )[:limit]


def classify_client(user_agent: str) -> dict[str, str | bool]:
    ua = (user_agent or "").strip()
    lower = ua.lower()
    bot_markers = {
        "bot": "Bot 标识", "spider": "Spider 爬虫", "crawler": "Crawler 爬虫",
        "slurp": "搜索爬虫", "bingpreview": "Bing 预览", "headless": "无头浏览器",
        "monitoring": "监控程序", "healthcheck": "健康检查", "zgrab": "ZGrab 扫描",
        "masscan": "Masscan 扫描", "nmap": "Nmap 扫描", "nikto": "Nikto 扫描",
        "sqlmap": "SQLMap 扫描", "curl/": "curl 请求", "wget/": "wget 请求",
        "python-requests": "Python 请求", "python-urllib": "Python 请求",
        "go-http-client": "Go 请求", "libwww-perl": "脚本请求",
        "scrapy": "Scrapy 爬虫", "aiohttp": "Python 请求",
    }
    bot_reason = "缺少浏览器标识" if not ua else ""
    if not bot_reason:
        for marker, label in bot_markers.items():
            if marker in lower:
                bot_reason = label
                break

    browser = "未知"
    browser_patterns = (
        ("edg/", "Edge"), ("opr/", "Opera"), ("crios/", "Chrome"),
        ("chrome/", "Chrome"), ("fxios/", "Firefox"), ("firefox/", "Firefox"),
        ("version/", "Safari"),
    )
    for marker, name in browser_patterns:
        match = re.search(re.escape(marker) + r"([\d.]+)", lower)
        if match and (name != "Safari" or "safari/" in lower):
            browser = f"{name} {match.group(1).split('.')[0]}"
            break
    if bot_reason:
        browser = "自动程序"

    operating_system = "未知"
    windows_versions = {
        "windows nt 10.0": "Windows 10/11",
        "windows nt 6.3": "Windows 8.1",
        "windows nt 6.1": "Windows 7",
    }
    for marker, name in windows_versions.items():
        if marker in lower:
            operating_system = name
            break
    if "android" in lower:
        operating_system = "Android"
    elif any(marker in lower for marker in ("iphone", "ipad", "ios")):
        operating_system = "iOS / iPadOS"
    elif "mac os x" in lower:
        operating_system = "macOS"
    elif "linux" in lower and operating_system == "未知":
        operating_system = "Linux"

    if bot_reason:
        device = "自动程序"
    elif "ipad" in lower or "tablet" in lower:
        device = "平板"
    elif any(marker in lower for marker in ("mobile", "iphone", "android")):
        device = "手机"
    elif any(marker in lower for marker in ("windows", "macintosh", "linux", "x11")):
        device = "电脑"
    else:
        device = "未知设备"
    return {
        "is_bot": bool(bot_reason),
        "bot_reason": bot_reason,
        "browser": browser,
        "operating_system": operating_system,
        "device": device,
    }


def masked_client_ip() -> str:
    from moderation import client_ip
    raw_ip = client_ip()
    try:
        address = ipaddress.ip_address(raw_ip)
    except ValueError:
        return ""
    if address.version == 4:
        parts = str(address).split(".")
        return ".".join(parts[:3] + ["*"])
    parts = address.exploded.split(":")
    return ":".join(parts[:4]) + "::*"


def visitor_fingerprint(is_bot: bool = False) -> str | None:
    if is_bot:
        return None
    visitor_id = session.get("visitor_id")
    if not visitor_id:
        visitor_id = secrets.token_urlsafe(24)
        session["visitor_id"] = visitor_id
        session.permanent = True
    secret = str(app.config["SECRET_KEY"]).encode("utf-8")
    return hmac.new(secret, visitor_id.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def referrer_hostname() -> str:
    referrer = request.referrer or ""
    if not referrer:
        return ""
    try:
        hostname = (urlparse(referrer).hostname or "").lower()[:120]
    except ValueError:
        return ""
    if hostname == request.host.split(":", 1)[0].lower():
        return ""
    return hostname


def track_pageview(path: str) -> None:
    day = datetime.now(SHANGHAI).strftime("%Y-%m-%d")
    db = get_db()
    timestamp = now_iso()
    user_agent = request.headers.get("User-Agent", "")[:500]
    client = classify_client(user_agent)
    visitor_hash = visitor_fingerprint(bool(client["is_bot"]))
    event_cursor = db.execute(
        """
        INSERT INTO visit_events
        (visited_at, day, visitor_hash, path, referrer_host, user_agent,
         browser, operating_system, device, ip_masked, is_bot, bot_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            timestamp,
            day,
            visitor_hash or "",
            path[:240],
            referrer_hostname(),
            user_agent,
            client["browser"],
            client["operating_system"],
            client["device"],
            masked_client_ip(),
            int(bool(client["is_bot"])),
            client["bot_reason"],
        ),
    )
    if not client["is_bot"]:
        session["pending_visit_event_id"] = event_cursor.lastrowid
        db.execute(
            """
            INSERT INTO daily_stats (day, path, views) VALUES (?, ?, 1)
            ON CONFLICT(day, path) DO UPDATE SET views = views + 1
            """,
            (day, path),
        )
    if visitor_hash:
        db.execute(
            """
            INSERT INTO visitor_daily
            (day, visitor_hash, first_seen, last_seen, page_views,
             first_path, last_path, referrer_host)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(day, visitor_hash) DO UPDATE SET
                last_seen = excluded.last_seen,
                page_views = visitor_daily.page_views + 1,
                last_path = excluded.last_path
            """,
            (
                day,
                visitor_hash,
                timestamp,
                timestamp,
                path[:240],
                path[:240],
                referrer_hostname(),
            ),
        )
    db.commit()


def analytics_path_label(path: str, db: sqlite3.Connection) -> str:
    labels = {
        "/": "首页",
        "/articles": "文章列表",
        "/projects": "项目",
        "/toolbox": "工具箱",
        "/notes": "片段",
        "/about": "关于",
        "/archive": "全部记录",
        "/garden": "内容地图",
        "/uses": "工具箱",
        "/colophon": "关于本站",
    }
    if path in labels:
        return labels[path]
    if path.startswith("/posts/"):
        slug = path.removeprefix("/posts/")
        item = db.execute(
            "SELECT title FROM posts WHERE slug = ?", (slug,)
        ).fetchone()
        return item["title"] if item else f"文章 · {slug}"
    if path.startswith("/projects/"):
        slug = path.removeprefix("/projects/")
        item = db.execute(
            "SELECT title FROM projects WHERE slug = ?", (slug,)
        ).fetchone()
        return item["title"] if item else f"项目 · {slug}"
    return path


@app.template_filter("project_status")
def project_status(value: str) -> str:
    return {"idea": "构思中", "building": "进行中", "shipped": "已完成"}.get(value, value)


@app.context_processor
def inject_site_context():
    return {
        "site_settings": get_settings(),
        "current_year": datetime.now(SHANGHAI).year,
        "has_og_image": (BASE_DIR / "static" / "og.png").exists(),
    }


def listing_window(total):
    pages=max(1,(total+9)//10)
    try:
        page=max(1,min(pages,int(request.args.get('page','1'))))
    except ValueError:
        page=1
    return page,pages


@app.get("/")
def home():
    track_pageview("/")
    query = request.args.get("q", "").strip()
    active_tag = request.args.get("tag", "").strip()
    active_category = request.args.get("category", "").strip()
    where = ["status = 'published'"]
    params: list[str] = []
    if query:
        where.append("(title LIKE ? OR excerpt LIKE ? OR content LIKE ? OR tags LIKE ?)")
        wildcard = f"%{query}%"
        params.extend([wildcard] * 4)
    if active_tag:
        where.append("tags LIKE ?")
        params.append(f"%{active_tag}%")
    if active_category:
        where.append("category = ?")
        params.append(active_category)

    project_where = ["is_visible = 1"]
    project_params: list[str] = []
    if query:
        project_where.append("(title LIKE ? OR summary LIKE ? OR content LIKE ? OR stack LIKE ?)")
        project_params.extend([f"%{query}%"] * 4)
    if active_tag:
        project_where.append("stack LIKE ?")
        project_params.append(f"%{active_tag}%")
    if active_category:
        project_where.append("0")
    post_condition = ' AND '.join(where)
    project_condition = ' AND '.join(project_where)
    matching = get_db().execute(
        f"SELECT (SELECT COUNT(*) FROM posts WHERE {post_condition}) + "
        f"(SELECT COUNT(*) FROM projects WHERE {project_condition})",
        (*params, *project_params),
    ).fetchone()[0]
    page,pages=listing_window(matching)
    posts = get_db().execute(
        f"""SELECT id,title,slug,excerpt,content,tags,category,is_featured,views,
                   published_at,created_at,updated_at,'post' AS entry_type,0 AS sort_order
            FROM posts WHERE {post_condition}
            UNION ALL
            SELECT id,title,slug,summary AS excerpt,content,stack AS tags,'项目' AS category,
                   is_featured,0 AS views,created_at AS published_at,created_at,updated_at,
                   'project' AS entry_type,sort_order
            FROM projects WHERE {project_condition}
            ORDER BY is_featured DESC,published_at DESC,sort_order DESC,id DESC,entry_type
            LIMIT ? OFFSET ?""",
        (*params,*project_params,10,(page-1)*10),
    ).fetchall()
    all_posts = get_db().execute(
        "SELECT id,title,slug,excerpt,tags,category,is_featured,views,published_at,created_at,updated_at FROM posts WHERE status = 'published' ORDER BY published_at DESC, id DESC"
    ).fetchall()
    categories = get_db().execute(
        """
        SELECT category, COUNT(*) AS count FROM posts
        WHERE status = 'published' GROUP BY category ORDER BY count DESC, category
        """
    ).fetchall()
    tag_counts: dict[str, int] = {}
    for item in all_posts:
        for tag in split_tags(item["tags"]):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    featured = next((item for item in all_posts if item["is_featured"]), all_posts[0] if all_posts else None)
    filtered_posts = [item for item in posts if not featured or item["id"] != featured["id"]]
    recent_notes = get_db().execute(
        "SELECT * FROM notes WHERE status = 'published' ORDER BY created_at DESC, id DESC LIMIT 5"
    ).fetchall()
    featured_projects = get_db().execute(
        """
        SELECT * FROM projects WHERE is_visible = 1
        ORDER BY is_featured DESC, sort_order DESC, updated_at DESC LIMIT 4
        """
    ).fetchall()
    visible_project_count = get_db().execute(
        "SELECT COUNT(*) AS n FROM projects WHERE is_visible = 1"
    ).fetchone()["n"]
    published_note_count = get_db().execute(
        "SELECT COUNT(*) AS n FROM notes WHERE status = 'published'"
    ).fetchone()["n"]
    total_views = get_db().execute(
        "SELECT COALESCE(SUM(views), 0) AS n FROM posts WHERE status = 'published'"
    ).fetchone()["n"]
    update_candidates = [item["updated_at"] for item in all_posts]
    update_candidates.extend(item["updated_at"] for item in featured_projects)
    update_candidates.extend(item["updated_at"] for item in recent_notes)
    category_details = []
    for category in categories:
        latest = next(
            (item for item in all_posts if item["category"] == category["category"]),
            None,
        )
        category_details.append(
            {
                "name": category["category"],
                "count": category["count"],
                "latest": latest["title"] if latest else "",
            }
        )
    return render_template(
        "home.html",
        posts=posts,
        listing_page=page,listing_pages=pages,
        featured=featured if not (query or active_tag or active_category) else None,
        total_posts=len(all_posts),
        categories=categories,
        tag_counts=sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))[:12],
        query=query,
        active_tag=active_tag,
        active_category=active_category,
        recent_notes=recent_notes,
        featured_projects=featured_projects,
        latest_posts=all_posts[:3],
        category_details=category_details,
        recent_activity=get_recent_activity(6),
        dashboard_stats={
            "posts": len(all_posts),
            "projects": visible_project_count,
            "notes": published_note_count,
            "views": total_views,
            "latest_update": max(update_candidates) if update_candidates else now_iso(),
        },
    )


@app.get("/articles")
def articles():
    track_pageview("/articles")
    query = request.args.get("q", "").strip()
    active_tag = request.args.get("tag", "").strip()
    active_category = request.args.get("category", "").strip()
    where = ["status = 'published'"]
    params: list[str] = []
    if query:
        where.append("(title LIKE ? OR excerpt LIKE ? OR content LIKE ? OR tags LIKE ?)")
        wildcard = f"%{query}%"
        params.extend([wildcard] * 4)
    if active_tag:
        where.append("tags LIKE ?")
        params.append(f"%{active_tag}%")
    if active_category:
        where.append("category = ?")
        params.append(active_category)

    matching=get_db().execute(f"SELECT COUNT(*) FROM posts WHERE {' AND '.join(where)}",params).fetchone()[0]
    page,pages=listing_window(matching)
    posts = get_db().execute(
        f"""
        SELECT * FROM posts
        WHERE {' AND '.join(where)}
        ORDER BY is_featured DESC, published_at DESC, id DESC LIMIT ? OFFSET ?
        """,
        (*params,10,(page-1)*10),
    ).fetchall()
    all_posts = get_db().execute(
        """
        SELECT tags FROM posts
        WHERE status = 'published'
        ORDER BY published_at DESC, id DESC
        """
    ).fetchall()
    categories = get_db().execute(
        """
        SELECT category, COUNT(*) AS count FROM posts
        WHERE status = 'published'
        GROUP BY category
        ORDER BY count DESC, category
        """
    ).fetchall()
    tag_counts: dict[str, int] = {}
    for item in all_posts:
        for tag in split_tags(item["tags"]):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return render_template(
        "articles.html",
        posts=posts,
        listing_page=page,listing_pages=pages,
        total_posts=len(all_posts),
        categories=categories,
        tag_counts=sorted(
            tag_counts.items(), key=lambda item: (-item[1], item[0])
        )[:16],
        query=query,
        active_tag=active_tag,
        active_category=active_category,
    )


@app.get("/posts/<slug>")
def post_detail(slug: str):
    db = get_db()
    post = db.execute(
        "SELECT * FROM posts WHERE slug = ? AND status = 'published'", (slug,)
    ).fetchone()
    if post is None:
        abort(404)
    track_pageview(f"/posts/{slug}")
    app.extensions["community"]["record_view"](post["id"])
    post = db.execute("SELECT * FROM posts WHERE id = ?", (post["id"],)).fetchone()
    try:
        article_html, article_toc = article_bundle(post["content"], post["content_format"])
    except LatexRenderError:
        article_html = '<p>LaTeX 排版暂不可用，以下是文章源码。</p><pre><code>' + xml_escape(post['content']) + '</code></pre>'
        article_toc = ''

    current_tags = set(split_tags(post["tags"]))
    candidates = db.execute(
        "SELECT id,title,slug,category,tags,published_at FROM posts WHERE status = 'published' AND id != ? ORDER BY published_at DESC",
        (post["id"],),
    ).fetchall()
    related = sorted(
        candidates,
        key=lambda item: (
            int(item["category"] == post["category"]) * 3
            + len(current_tags.intersection(split_tags(item["tags"]))),
            item["published_at"],
        ),
        reverse=True,
    )[:3]
    return render_template(
        "post.html", post=post, article_html=article_html, article_toc=article_toc, related=related,
        community=app.extensions["community"]["post_data"](post["id"])
    )


@app.get("/archive")
def archive():
    track_pageview("/archive")
    db = get_db()
    posts = db.execute(
        "SELECT title,slug,excerpt,category,published_at FROM posts WHERE status = 'published' ORDER BY published_at DESC, id DESC"
    ).fetchall()
    projects = db.execute(
        "SELECT title,slug,summary,status,updated_at FROM projects WHERE is_visible = 1 ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    notes_count = db.execute("SELECT COUNT(*) FROM notes WHERE status = 'published'").fetchone()[0]
    timeline: list[dict[str, str]] = []
    for post in posts:
        timeline.append(
            {
                "kind": "文章",
                "title": post["title"],
                "summary": post["excerpt"],
                "meta": post["category"],
                "date": post["published_at"],
                "url": url_for("post_detail", slug=post["slug"]),
            }
        )
    for project in projects:
        timeline.append(
            {
                "kind": "项目",
                "title": project["title"],
                "summary": "",
                "meta": project_status(project["status"]),
                "date": project["updated_at"],
                "url": url_for("project_detail", slug=project["slug"]),
            }
        )
    for note in []:  # Notes feature retired; stored data retained.
        timeline.append(
            {
                "kind": "片段",
                "title": note["content"],
                "summary": "一条被保存下来的现场想法。",
                "meta": note["mood"],
                "date": note["created_at"],
                "url": url_for("notes") + f"#note-{note['id']}",
            }
        )
    timeline.sort(key=lambda item: item["date"], reverse=True)
    grouped: dict[str, list[dict[str, str]]] = {}
    for item in timeline:
        try:
            year = datetime.fromisoformat(item["date"]).astimezone(SHANGHAI).strftime("%Y")
        except (ValueError, TypeError):
            year = item["date"][:4] or "未分类"
        grouped.setdefault(year, []).append(item)
    return render_template(
        "archive.html",
        grouped=grouped,
        total=len(timeline),
        stats={"posts": len(posts), "projects": len(projects), "notes": notes_count},
    )


@app.get("/projects")
def projects():
    track_pageview("/projects")
    items = get_db().execute(
        """
        SELECT * FROM projects WHERE is_visible = 1
        ORDER BY is_featured DESC, sort_order DESC, year DESC, updated_at DESC
        """
    ).fetchall()
    return render_template("projects.html", projects=items)


@app.get("/projects/<slug>")
def project_detail(slug: str):
    project = get_db().execute(
        "SELECT * FROM projects WHERE slug = ? AND is_visible = 1", (slug,)
    ).fetchone()
    if project is None:
        abort(404)
    track_pageview(f"/projects/{slug}")
    app.extensions["community"]["record_project_view"](project["id"])
    project_view_row = get_db().execute(
        "SELECT views FROM project_view_counts WHERE project_id = ?", (project["id"],)
    ).fetchone()
    project_views = project_view_row["views"] if project_view_row else 0
    pdf_url=None
    if project['content_format']=='latex' and project_workspace_id(get_db(),project['id']):
        published=get_db().execute('SELECT value FROM settings WHERE key=?',('latex.pdf.project.'+str(project['id']),)).fetchone()
        if published and re.fullmatch(r'/uploads/latex-\d+-[a-f0-9]+\.pdf',published['value']):pdf_url=published['value']
    try:
        content_html, content_toc = (latex_bundle(project["content"], number_sections=True)
                                     if project["content_format"] == "latex"
                                     else article_bundle(project["content"], project["content_format"]))
    except LatexRenderError:
        content_html,content_toc='','' 
    return render_template(
        "project.html", project=project, community=app.extensions["community"]["project_data"](project["id"]), project_views=project_views, content_html=content_html, content_toc=content_toc,pdf_url=pdf_url
    )


@app.get("/notes")
def notes():
    track_pageview("/notes")
    items = get_db().execute(
        "SELECT * FROM notes WHERE status = 'published' ORDER BY created_at DESC, id DESC"
    ).fetchall()
    moods = get_db().execute(
        """
        SELECT mood, COUNT(*) AS count FROM notes WHERE status = 'published'
        GROUP BY mood ORDER BY count DESC, mood
        """
    ).fetchall()
    return render_template("notes.html", notes=items, moods=moods)


@app.get("/garden")
def garden():
    track_pageview("/garden")
    db = get_db()
    posts = db.execute(
        "SELECT title,slug,category,tags,published_at,content FROM posts WHERE status = 'published' ORDER BY published_at DESC, id DESC"
    )
    projects_count = db.execute(
        "SELECT COUNT(*) AS n FROM projects WHERE is_visible = 1"
    ).fetchone()["n"]
    notes_count = db.execute(
        "SELECT COUNT(*) AS n FROM notes WHERE status = 'published'"
    ).fetchone()["n"]
    posts_by_category: dict[str, list[dict[str, object]]] = {}
    tag_counts: dict[str, int] = {}
    for post in posts:
        item = {key: post[key] for key in ('title','slug','category','tags','published_at')}
        item['reading_minutes'] = reading_minutes(post['content'])
        posts_by_category.setdefault(post["category"], []).append(item)
        for tag in split_tags(post["tags"]):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    return render_template(
        "garden.html",
        posts_by_category=posts_by_category,
        tag_counts=sorted(tag_counts.items(), key=lambda item: (-item[1], item[0])),
        recent_activity=get_recent_activity(10),
        stats={
            "posts": sum(len(items) for items in posts_by_category.values()),
            "projects": projects_count,
            "notes": notes_count,
            "topics": len(posts_by_category),
        },
    )


@app.get("/uses")
def uses():
    track_pageview("/uses")
    return render_template(
        "uses.html",
        skills=split_tags(get_settings().get("skills", "")),
    )


@app.get("/colophon")
def colophon():
    track_pageview("/colophon")
    db = get_db()
    stats = {
        "posts": db.execute(
            "SELECT COUNT(*) AS n FROM posts WHERE status = 'published'"
        ).fetchone()["n"],
        "projects": db.execute(
            "SELECT COUNT(*) AS n FROM projects WHERE is_visible = 1"
        ).fetchone()["n"],
        "notes": db.execute(
            "SELECT COUNT(*) AS n FROM notes WHERE status = 'published'"
        ).fetchone()["n"],
    }
    return render_template("colophon.html", stats=stats)


TOOLBOX_INSTALLERS = {'uu': {'bytes': 92252208, 'icon': 'static/toolbox/uu.png', 'slug': 'uu', 'filename': 'uuyc_4.44.0.exe', 'sha256': '4f1b86d768478ddb923ca13c19c9880976549edc0d26b3c8067c87f7fb4c5379', 'title': 'UU远程', 'size_label': '88.0 MB'}, 'sunlogin': {'bytes': 198984640, 'icon': 'static/toolbox/sunlogin.png', 'slug': 'sunlogin', 'filename': 'AweSun_16.6.0.32198.exe', 'sha256': 'be592d004bc99ce2043ecf29182a173335e8197712dcdec0b013093d38577ba1', 'title': '向日葵远程', 'size_label': '189.8 MB'}, 'todesk': {'bytes': 4144904, 'icon': 'static/toolbox/todesk.png', 'slug': 'todesk', 'filename': 'ToDesk_Installer.exe', 'sha256': '13292a5f3e870a33999f943f5ac2df456aae3c7815bd850b126f0ec60f50b883', 'title': 'ToDesk远程', 'size_label': '4.0 MB'}}


@app.get("/toolbox")
def toolbox():
    track_pageview("/toolbox")
    return render_template("toolbox.html", tools=TOOLBOX_INSTALLERS.values())


@app.get("/toolbox/download/<slug>")
def toolbox_download(slug: str):
    tool = TOOLBOX_INSTALLERS.get(slug)
    if tool is None:
        abort(404)
    return send_from_directory(
        BASE_DIR / "toolbox-downloads", tool["filename"],
        as_attachment=True, download_name=tool["filename"],
        mimetype="application/octet-stream", conditional=True, max_age=86400,
    )


@app.get("/about")
def about():
    track_pageview("/about")
    project_count = get_db().execute(
        "SELECT COUNT(*) AS n FROM projects WHERE is_visible = 1"
    ).fetchone()["n"]
    post_count = get_db().execute(
        "SELECT COUNT(*) AS n FROM posts WHERE status = 'published'"
    ).fetchone()["n"]
    return render_template("about.html", project_count=project_count, post_count=post_count)


@app.get("/uploads/<path:filename>")
def uploaded_file(filename: str):
    return send_from_directory(UPLOAD_DIR, filename, max_age=31536000)


@app.get("/feed.xml")
def feed():
    posts = get_db().execute(
        "SELECT title,slug,excerpt,CASE WHEN excerpt='' THEN content ELSE '' END AS content,published_at FROM posts WHERE status = 'published' ORDER BY published_at DESC LIMIT 20"
    ).fetchall()
    root = request.url_root.rstrip("/")
    items = []
    for post in posts:
        link = f"{root}{url_for('post_detail', slug=post['slug'])}"
        items.append(
            "<item>"
            f"<title>{xml_escape(post['title'])}</title>"
            f"<link>{xml_escape(link)}</link><guid>{xml_escape(link)}</guid>"
            f"<description>{xml_escape(post['excerpt'] or plain_excerpt(post['content']))}</description>"
            f"<pubDate>{xml_escape(post['published_at'])}</pubDate>"
            "</item>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>Orange手记</title>'
        f"<link>{xml_escape(root)}</link><description>记录代码、模型与生活现场。</description>"
        + "".join(items) + "</channel></rss>"
    )
    return Response(xml, mimetype="application/rss+xml")


@app.get("/sitemap.xml")
def sitemap():
    root = request.url_root.rstrip("/")
    posts = get_db().execute(
        "SELECT slug, updated_at FROM posts WHERE status = 'published' ORDER BY updated_at DESC"
    ).fetchall()
    projects = get_db().execute(
        "SELECT slug, updated_at FROM projects WHERE is_visible = 1 ORDER BY updated_at DESC"
    ).fetchall()
    urls = [
        f"<url><loc>{xml_escape(root + path)}</loc></url>"
        for path in ["/", url_for("articles"), url_for("garden"), url_for("uses"), url_for("colophon"), url_for("archive"), url_for("projects"), url_for("toolbox"), url_for("about")]
    ]
    urls.extend(
        f"<url><loc>{xml_escape(root + url_for('post_detail', slug=post['slug']))}</loc><lastmod>{xml_escape(post['updated_at'][:10])}</lastmod></url>"
        for post in posts
    )
    urls.extend(
        f"<url><loc>{xml_escape(root + url_for('project_detail', slug=project['slug']))}</loc><lastmod>{xml_escape(project['updated_at'][:10])}</lastmod></url>"
        for project in projects
    )
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(urls) + "</urlset>",
        mimetype="application/xml",
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/analytics/confirm")
def analytics_confirm():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return {"ok": False, "message": "请求正文必须是有效的 JSON 对象。"}, 400
    event_id = payload.get("event_id", session.get("pending_visit_event_id"))
    if type(event_id) is not int or not 0 < event_id < 2**63 or not session.get("visitor_id"):
        return {"ok": False}, 204
    visitor_hash = visitor_fingerprint()
    screen_size = str(payload.get("screen", ""))[:32]
    language = str(payload.get("language", ""))[:32]
    timezone_name = str(payload.get("timezone", ""))[:64]
    db = get_db()
    db.execute(
        """
        UPDATE visit_events
        SET js_verified = 1, screen_size = ?, language = ?, timezone_name = ?
        WHERE id = ? AND visitor_hash = ? AND is_bot = 0
        """,
        (screen_size, language, timezone_name, event_id, visitor_hash),
    )
    db.commit()
    return {"ok": True}


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        validate_csrf()
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if len(username) > 64 or len(password) > 128:
            abort(400)
        from moderation import client_ip
        try:
            reserve_admin_login(app, get_db(), client_ip(), unicodedata.normalize('NFKC', username).casefold())
        except LoginLimited as error:
            app.logger.warning('Administrator login rate limited: %s', error.scope)
            response = app.make_response((render_template('admin_login.html'), 429))
            response.headers['Retry-After'] = str(error.retry_after)
            return response
        except sqlite3.DatabaseError:
            return {"ok": False, "message": "服务暂时繁忙，请稍后重试。"}, 503
        valid_user = constant_time_text_equal(username, ADMIN_USER)
        valid_password = bool(ADMIN_PASSWORD_HASH) and check_password_hash(
            ADMIN_PASSWORD_HASH, password
        )
        if valid_user and valid_password:
            session.clear()
            session._fresh_admin_auth = True
            session["admin_authenticated"] = True
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("admin_dashboard"))
        flash("用户名或密码不正确。", "error")
    return render_template("admin_login.html")


@app.get("/admin/quick-login/<token>")
def admin_quick_login(token: str):
    # Legacy URL bearer credentials are never accepted, including AUTO_TOKEN.
    abort(404)


@app.route('/admin/quick-login', methods=['GET', 'POST'])
def admin_ticket_login():
    if request.method == 'GET':
        response = app.make_response(render_template('admin_ticket_login.html'))
    else:
        if not ADMIN_PASSWORD_HASH:
            abort(404)
        try:
            from moderation import client_ip
            reserve_admin_login(app, get_db(), client_ip(), '\0ticket-consume')
            accepted = consume_admin_ticket(app, get_db(), request.form.get('ticket', ''))
        except LoginLimited as error:
            response = app.make_response(({"ok": False, "message": "操作太频繁，请稍后再试。"}, 429))
            response.headers['Retry-After'] = str(error.retry_after)
            return response
        except sqlite3.DatabaseError:
            return {"ok": False, "message": "服务暂时繁忙，请稍后重试。"}, 503
        if not accepted:
            abort(400, '登录凭证无效、已使用或过期，请重新获取。')
        session.clear()
        session._fresh_admin_auth = True
        session['admin_authenticated'] = True
        session['csrf_token'] = secrets.token_urlsafe(32)
        response = redirect(url_for('admin_dashboard'))
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@app.post("/admin/logout")
@admin_required
def admin_logout():
    validate_csrf()
    session.clear()
    return redirect(url_for("admin_login"))


@app.get("/admin")
@admin_required
def admin_dashboard():
    status_filter = request.args.get("status", "").strip()
    query = request.args.get("q", "").strip()
    where: list[str] = []
    params: list[str] = []
    if status_filter in {"draft", "published"}:
        where.append("status = ?")
        params.append(status_filter)
    if query:
        where.append("(title LIKE ? OR tags LIKE ? OR category LIKE ?)")
        params.extend([f"%{query}%"] * 3)
    sql = "SELECT * FROM posts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC, id DESC"
    db = get_db()
    posts = db.execute(sql, params).fetchall()
    stats = db.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published,
               SUM(CASE WHEN status='draft' THEN 1 ELSE 0 END) AS drafts,
               COALESCE(SUM(views), 0) AS views
        FROM posts
        """
    ).fetchone()
    content_stats = {
        "projects": db.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"],
        "notes": db.execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"],
        "media": db.execute("SELECT COUNT(*) AS n FROM media").fetchone()["n"],
    }
    today_date = datetime.now(SHANGHAI).date()
    today = today_date.isoformat()
    seven_days_ago = (today_date - timedelta(days=6)).isoformat()
    thirty_days_ago = (today_date - timedelta(days=29)).isoformat()
    active_cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=5)
    ).replace(microsecond=0).isoformat()
    today_visitors = db.execute(
        """
        SELECT COUNT(*) AS visitors, COALESCE(SUM(page_views), 0) AS views
        FROM visitor_daily WHERE day = ?
        """,
        (today,),
    ).fetchone()
    visitor_stats = {
        "today": today_visitors["visitors"],
        "today_views": today_visitors["views"],
        "seven_days": db.execute(
            "SELECT COUNT(DISTINCT visitor_hash) AS n FROM visitor_daily WHERE day >= ?",
            (seven_days_ago,),
        ).fetchone()["n"],
        "thirty_days": db.execute(
            "SELECT COUNT(DISTINCT visitor_hash) AS n FROM visitor_daily WHERE day >= ?",
            (thirty_days_ago,),
        ).fetchone()["n"],
        "total": db.execute(
            "SELECT COUNT(DISTINCT visitor_hash) AS n FROM visitor_daily"
        ).fetchone()["n"],
        "active": db.execute(
            "SELECT COUNT(DISTINCT visitor_hash) AS n FROM visitor_daily WHERE last_seen >= ?",
            (active_cutoff,),
        ).fetchone()["n"],
        "returning": db.execute(
            """
            SELECT COUNT(*) AS n FROM (
                SELECT visitor_hash FROM visitor_daily
                WHERE day >= ? GROUP BY visitor_hash HAVING COUNT(*) >= 2
            )
            """,
            (thirty_days_ago,),
        ).fetchone()["n"],
    }
    view_rows = db.execute(
        """
        SELECT day, SUM(views) AS views FROM daily_stats WHERE day >= ?
        GROUP BY day
        """,
        ((today_date - timedelta(days=13)).isoformat(),),
    ).fetchall()
    visitor_rows = db.execute(
        """
        SELECT day, COUNT(*) AS visitors FROM visitor_daily WHERE day >= ?
        GROUP BY day
        """,
        ((today_date - timedelta(days=13)).isoformat(),),
    ).fetchall()
    views_by_day = {item["day"]: item["views"] for item in view_rows}
    visitors_by_day = {item["day"]: item["visitors"] for item in visitor_rows}
    traffic = [
        {
            "day": (today_date - timedelta(days=offset)).isoformat(),
            "views": views_by_day.get(
                (today_date - timedelta(days=offset)).isoformat(), 0
            ),
            "visitors": visitors_by_day.get(
                (today_date - timedelta(days=offset)).isoformat(), 0
            ),
        }
        for offset in range(13, -1, -1)
    ]
    max_traffic = max(
        [max(item["views"], item["visitors"]) for item in traffic] + [1]
    )
    for item in traffic:
        item["view_percent"] = round(item["views"] / max_traffic * 100, 1)
        item["visitor_percent"] = round(
            item["visitors"] / max_traffic * 100, 1
        )
    popular_articles = db.execute(
        """
        SELECT p.title AS label, '/posts/' || p.slug AS path, SUM(d.views) AS views
        FROM daily_stats AS d
        JOIN posts AS p ON d.path = '/posts/' || p.slug
        WHERE p.status = 'published' AND d.day >= ? AND d.day <= ?
        GROUP BY p.id, p.slug, p.title
        HAVING SUM(d.views) > 0
        ORDER BY views DESC, p.id DESC LIMIT 5
        """,
        (thirty_days_ago, today_date.isoformat()),
    ).fetchall()
    referrers = db.execute(
        """
        SELECT CASE WHEN referrer_host = '' THEN '直接访问' ELSE referrer_host END AS source,
               COUNT(*) AS visitors
        FROM visitor_daily WHERE day >= ?
        GROUP BY source ORDER BY visitors DESC, source LIMIT 6
        """,
        (thirty_days_ago,),
    ).fetchall()
    popular = db.execute(
        """
        SELECT id, title, slug, views FROM posts WHERE status = 'published'
        ORDER BY views DESC, published_at DESC LIMIT 5
        """
    ).fetchall()
    category_stats = db.execute(
        """
        SELECT category, COUNT(*) AS count, COALESCE(SUM(views), 0) AS views
        FROM posts WHERE status = 'published'
        GROUP BY category ORDER BY count DESC, category
        """
    ).fetchall()
    recent_operations = db.execute(
        "SELECT * FROM activity_log ORDER BY created_at DESC, id DESC LIMIT 8"
    ).fetchall()
    trash_count = db.execute(
        "SELECT COUNT(*) AS n FROM trash_items"
    ).fetchone()["n"]
    return render_template(
        "admin_dashboard.html", posts=posts, stats=stats, content_stats=content_stats,
        traffic=traffic, popular=popular, category_stats=category_stats,
        visitor_stats=visitor_stats, popular_articles=popular_articles, referrers=referrers,
        recent_operations=recent_operations, trash_count=trash_count,
        status_filter=status_filter, query=query
    )


@app.get("/admin/visitors")
@admin_required
def admin_visitors():
    db = get_db()
    query = request.args.get("q", "").strip()[:80]
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    per_page = 40
    where = ["is_bot = 0 AND js_verified = 1"]
    params: list[str | int] = []
    if query:
        term = query[2:] if query.upper().startswith("V-") else query
        where.append("(browser LIKE ? OR device LIKE ? OR operating_system LIKE ? OR visitor_hash LIKE ?)")
        params.extend([f"%{term}%"] * 4)
    where_sql = " WHERE " + " AND ".join(where)
    totals = db.execute(
        f"""SELECT COUNT(DISTINCT COALESCE(NULLIF(visitor_hash, ''), 'event:' || id)) AS visitors,
                   COUNT(*) AS visits FROM visit_events{where_sql}""", params
    ).fetchone()
    total = totals["visitors"]
    total_visits = totals["visits"]
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    rows = db.execute(
        f"""
        WITH filtered AS (
            SELECT *, COALESCE(NULLIF(visitor_hash, ''), 'event:' || id) AS visitor_key
            FROM visit_events{where_sql}
        ), ranked AS (
            SELECT *, COUNT(*) OVER (PARTITION BY visitor_key) AS visit_count,
                   ROW_NUMBER() OVER (PARTITION BY visitor_key ORDER BY visited_at DESC, id DESC) AS visitor_rank
            FROM filtered
        )
        SELECT * FROM ranked WHERE visitor_rank = 1
        ORDER BY visited_at DESC, id DESC LIMIT ? OFFSET ?
        """, [*params, per_page, (page - 1) * per_page],
    ).fetchall()
    visits = []
    for row in rows:
        item = dict(row)
        item["visitor_code"] = "V-" + item["visitor_hash"][:8].upper() if item["visitor_hash"] else "匿名访客"
        visits.append(item)
    return render_template(
        "admin_visitors.html", visits=visits, query=query,
        page=page, total_pages=total_pages, total=total, total_visits=total_visits,
    )


POST_REVISION_LIMIT = 5


def prune_post_revisions(db, post_id):
    db.execute(
        """DELETE FROM post_revisions WHERE post_id = ? AND id NOT IN (
            SELECT id FROM post_revisions WHERE post_id = ? ORDER BY id DESC LIMIT ?
        )""",
        (post_id, post_id, POST_REVISION_LIMIT),
    )


def save_post(post_id: int | None = None):
    creating = post_id is None
    title = request.form.get("title", "").strip()
    slug = normalize_slug(request.form.get("slug", ""))
    if creating and not request.form.get("slug", "").strip():
        slug += "-" + secrets.token_hex(3)
    excerpt = request.form.get("excerpt", "").strip()
    content = request.form.get("content", "")
    content_format = request.form.get('content_format', 'markdown')
    tags = ",".join(split_tags(request.form.get("tags", "")))
    category = request.form.get("category", "").strip() or "随笔"
    status = "published" if request.form.get("status") == "published" else "draft"
    is_featured = 1 if request.form.get("is_featured") == "1" else 0
    format_error = ''
    latex_html = None
    if content_format not in {'markdown', 'latex'}:
        format_error = '请选择 Markdown 或 LaTeX 编辑格式。'
    elif content_format == 'latex' and status == 'published':
        try:
            latex_html = latex_bundle(content)[0]
        except LatexRenderError as error:
            format_error = str(error) + ' 可切换为草稿保存源码。'
    if format_error:
        if request.headers.get('X-Orange-Save') == '1':
            return {'ok': False, 'message': format_error}, 422
        flash(format_error, 'error')
        return None
    if not title or not content.strip():
        if request.headers.get("X-Orange-Save") == "1":
            return {"ok": False, "message": "标题和正文不能为空。"}, 422
        flash("标题和正文不能为空。", "error")
        return None
    if not excerpt:
        if content_format == 'latex' and latex_html is not None:
            excerpt = plain_excerpt(bleach.clean(latex_html, tags=set(), attributes={}, strip=True))
        else:
            excerpt = plain_excerpt(content)
    db = get_db()
    timestamp = now_iso()
    try:
        if post_id is None:
            published_at = timestamp if status == "published" else ""
            db.execute(
                """
                INSERT INTO posts
                (title, slug, excerpt, content, content_format, tags, category, is_featured, views,
                 status, published_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (title, slug, excerpt, content, content_format, tags, category, is_featured,
                 status, published_at, timestamp, timestamp),
            )
            post_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        else:
            old = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
            if old is None:
                abort(404)
            if not request.form.get("slug", "").strip():
                slug = old['slug']
            if old["title"] != title or old["excerpt"] != excerpt or old["content"] != content or old['content_format'] != content_format:
                db.execute(
                    """
                    INSERT INTO post_revisions (post_id, title, excerpt, content, created_at, content_format)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (post_id, old["title"], old["excerpt"], old["content"], timestamp, old['content_format']),
                )
                prune_post_revisions(db, post_id)
            published_at = old["published_at"]
            if status == "published" and not published_at:
                published_at = timestamp
            db.execute(
                """
                UPDATE posts SET title=?, slug=?, excerpt=?, content=?, content_format=?, tags=?,
                    category=?, is_featured=?, status=?, published_at=?, updated_at=?
                WHERE id=?
                """,
                (title, slug, excerpt, content, content_format, tags, category, is_featured,
                 status, published_at, timestamp, post_id),
            )
        if is_featured:
            saved_id = post_id or db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            db.execute("UPDATE posts SET is_featured = 0 WHERE id != ?", (saved_id,))
        log_admin_action(
            "创建文章" if creating else "更新文章",
            "post",
            title,
            "已发布" if status == "published" else "草稿",
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        if request.headers.get("X-Orange-Save") == "1":
            return {"ok": False, "message": "链接别名已经存在，请换一个。"}, 409
        flash("链接别名已经存在，请换一个。", "error")
        return None
    if request.headers.get("X-Orange-Save") == "1":
        return {"ok": True, "edit_url": url_for("admin_edit", post_id=post_id), "slug": slug, "message": "文章已保存。"}
    flash("文章已保存。", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/new", methods=["GET", "POST"])
@admin_required
def admin_new():
    if request.method == "POST":
        validate_csrf()
        result = save_post()
        if result is not None:
            return result
    return render_template("admin_edit.html", post=None)


@app.route("/admin/edit/<int:post_id>", methods=["GET", "POST"])
@admin_required
def admin_edit(post_id: int):
    post = get_db().execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if post is None:
        abort(404)
    if request.method == "POST":
        validate_csrf()
        result = save_post(post_id)
        if result is not None:
            return result
        post = request.form
    return render_template("admin_edit.html", post=post)


@app.post("/admin/preview")
@admin_required
def admin_preview():
    validate_csrf()
    content = request.form.get("content", "")
    content_format = request.form.get('content_format', 'markdown')
    if content_format not in {'markdown', 'latex'}:
        return render_template('admin_preview_error.html', message='请选择有效的编辑格式。'), 422
    try:
        article_html, article_toc = article_bundle(content, content_format)
    except LatexRenderError as error:
        return render_template('admin_preview_error.html', message=str(error)), 422
    preview_time = now_iso()
    preview = SimpleNamespace(
        title=request.form.get("title", "").strip() or "未命名文章",
        excerpt=request.form.get("excerpt", "").strip() or plain_excerpt(content),
        content=content,
        content_format=content_format,
        tags=request.form.get("tags", ""),
        category=request.form.get("category", "").strip() or "随笔",
        published_at=preview_time,
        created_at=preview_time,
        updated_at=preview_time,
        views=0,
    )
    return render_template(
        "post.html", post=preview, article_html=article_html, article_toc=article_toc,
        related=[], preview_mode=True,
    )


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    if request.method == "POST":
        validate_csrf()
        values = [(key, request.form[key].strip()) for key in SETTING_DEFAULTS if key in request.form]
        get_db().executemany(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            values,
        )
        log_admin_action("更新网站资料", "settings", "全站资料")
        get_db().commit()
        flash("网站资料已更新。", "success")
        return redirect(url_for("admin_settings"))
    return render_template("admin_settings.html", settings=get_settings())


def save_project(project_id: int | None = None):
    creating = project_id is None
    def fail(message, status_code=422):
        if request.headers.get("X-Orange-Save") == "1":
            return {"ok": False, "message": message}, status_code
        flash(message, "error")
        return None
    title = request.form.get("title", "").strip()
    slug = normalize_slug(request.form.get("slug", ""))
    # Preserve the stored summary for older clients that omit the field.
    old_summary = get_db().execute('SELECT summary FROM projects WHERE id=?', (project_id,)).fetchone() if project_id is not None else None
    summary = request.form["summary"].strip() if "summary" in request.form else (old_summary["summary"] if old_summary else "")
    content = request.form.get("content", "")
    content_format = request.form.get("content_format", "markdown")
    if content_format not in {"markdown", "latex"}:
        return fail("请选择 Markdown 或 LaTeX 编辑格式。")
    linked_workspace=project_workspace_id(get_db(),project_id) if project_id is not None and content_format=='latex' else None
    if linked_workspace:
        content=get_db().execute('SELECT content FROM projects WHERE id=?',(project_id,)).fetchone()['content']
    if content_format == "latex" and not linked_workspace and request.form.get('latex_workbench')!='1' and request.form.get("is_visible") == "1":
        try:
            latex_bundle(content)
        except LatexRenderError as error:
            return fail(str(error) + " 可关闭公开展示后保存源码。")
    stack = ",".join(split_tags(request.form.get("stack", "")))
    status = request.form.get("status", "building")
    if status not in {"idea", "building", "shipped"}:
        status = "building"
    link = request.form.get("link", "").strip()
    if link and not re.match(r"^https?://", link, re.I):
        return fail("项目链接需要以 http:// 或 https:// 开头。")
    year = request.form.get("year", "").strip()[:10]
    is_featured = 1 if request.form.get("is_featured") == "1" else 0
    is_visible = 1 if request.form.get("is_visible") == "1" else 0
    try:
        sort_order = int(request.form.get("sort_order", "0") or 0)
    except ValueError:
        sort_order = 0
    if not title:
        return fail("项目名称不能为空。")
    timestamp = now_iso()
    db = get_db()
    try:
        if project_id is None:
            db.execute(
                """
                INSERT INTO projects
                (title, slug, summary, content, content_format, stack, status, link, year, is_featured,
                 is_visible, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (title, slug, summary, content, content_format, stack, status, link, year, is_featured,
                 is_visible, sort_order, timestamp, timestamp),
            )
            project_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        else:
            db.execute(
                """
                UPDATE projects SET title=?, slug=?, summary=?, content=?, content_format=?, stack=?,
                    status=?, link=?, year=?, is_featured=?, is_visible=?, sort_order=?, updated_at=?
                WHERE id=?
                """,
                (title, slug, summary, content, content_format, stack, status, link, year, is_featured,
                 is_visible, sort_order, timestamp, project_id),
            )
        db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('latex.editor.format.project.'+str(project_id),content_format))
        log_admin_action(
            "创建项目" if creating else "更新项目",
            "project",
            title,
            project_status(status),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return fail("项目链接别名已经存在。", 409)
    if request.headers.get("X-Orange-Save") == "1":
        return {"ok": True, "edit_url": url_for("admin_project_edit", project_id=project_id), "slug": slug, "message": "项目已保存。"}
    flash("项目已保存。", "success")
    return redirect(url_for("admin_projects"))


@app.post("/admin/projects/preview")
@admin_required
def admin_project_preview():
    validate_csrf()
    content_format = request.form.get('content_format', 'markdown')
    if content_format not in {'markdown', 'latex'}:
        return render_template('admin_preview_error.html', message='请选择有效的编辑格式。'), 422
    try:
        content_html, content_toc = article_bundle(request.form.get('content', ''), content_format)
    except LatexRenderError as error:
        return render_template('admin_preview_error.html', message=str(error)), 422
    project = SimpleNamespace(title=request.form.get('title', ''), summary=request.form.get('summary', ''),
                              year=request.form.get('year', ''), status=request.form.get('status', 'building'))
    return render_template('project.html', project=project, content_html=content_html, content_toc=content_toc, preview_mode=True)


@app.get("/admin/projects")
@admin_required
def admin_projects():
    items = get_db().execute(
        "SELECT * FROM projects ORDER BY sort_order DESC, updated_at DESC"
    ).fetchall()
    return render_template("admin_projects.html", projects=items)


@app.route("/admin/projects/new", methods=["GET", "POST"])
@admin_required
def admin_project_new():
    if request.method == "POST":
        validate_csrf()
        result = save_project()
        if result is not None:
            return result
    return render_template("admin_project_edit.html", project=request.form if request.method == "POST" else None)


@app.route("/admin/projects/edit/<int:project_id>", methods=["GET", "POST"])
@admin_required
def admin_project_edit(project_id: int):
    project = get_db().execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if project is None:
        abort(404)
    if request.method == "POST":
        validate_csrf()
        result = save_project(project_id)
        if result is not None:
            return result
        project = request.form
    preference=get_db().execute('SELECT value FROM settings WHERE key=?',('latex.editor.format.project.'+str(project_id),)).fetchone()
    editor_format=(preference['value'] if preference and preference['value'] in {'markdown','latex'} else ('latex' if project_workspace_id(get_db(),project_id) else project['content_format'])) if request.method=='GET' else project.get('content_format','markdown')
    return render_template("admin_project_edit.html", project=project,editor_format=editor_format)


@app.post("/admin/projects/delete/<int:project_id>")
@admin_required
def admin_project_delete(project_id: int):
    validate_csrf()
    deleted = move_content_to_trash("project", [project_id])
    flash("项目已移入回收站。" if deleted else "没有找到这个项目。", "success" if deleted else "error")
    return redirect(url_for("admin_projects"))


@app.post("/admin/projects/bulk-delete")
@admin_required
def admin_projects_bulk_delete():
    validate_csrf()
    selected = selected_content_ids()
    if not selected:
        flash("请先选择要删除的项目。", "error")
        return redirect(url_for("admin_projects"))
    deleted = move_content_to_trash("project", selected)
    flash(f"已将 {deleted} 个项目移入回收站。", "success")
    return redirect(url_for("admin_projects"))


def save_note(note_id: int | None = None):
    content = request.form.get("content", "").strip()
    mood = request.form.get("mood", "").strip() or "想法"
    status = "published" if request.form.get("status") == "published" else "draft"
    if not content:
        flash("片段内容不能为空。", "error")
        return None
    timestamp = now_iso()
    if note_id is None:
        get_db().execute(
            "INSERT INTO notes (content, mood, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (content, mood, status, timestamp, timestamp),
        )
    else:
        get_db().execute(
            "UPDATE notes SET content=?, mood=?, status=?, updated_at=? WHERE id=?",
            (content, mood, status, timestamp, note_id),
        )
    log_admin_action(
        "创建片段" if note_id is None else "更新片段",
        "note",
        content,
        mood,
    )
    get_db().commit()
    flash("片段已保存。", "success")
    return redirect(url_for("admin_notes"))


@app.get("/admin/notes")
@admin_required
def admin_notes():
    items = get_db().execute(
        "SELECT * FROM notes ORDER BY created_at DESC, id DESC"
    ).fetchall()
    return render_template("admin_notes.html", notes=items)


@app.route("/admin/notes/new", methods=["GET", "POST"])
@admin_required
def admin_note_new():
    if request.method == "POST":
        validate_csrf()
        result = save_note()
        if result is not None:
            return result
    return render_template("admin_note_edit.html", note=None)


@app.route("/admin/notes/edit/<int:note_id>", methods=["GET", "POST"])
@admin_required
def admin_note_edit(note_id: int):
    note = get_db().execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if note is None:
        abort(404)
    if request.method == "POST":
        validate_csrf()
        result = save_note(note_id)
        if result is not None:
            return result
        note = request.form
    return render_template("admin_note_edit.html", note=note)


@app.post("/admin/notes/delete/<int:note_id>")
@admin_required
def admin_note_delete(note_id: int):
    validate_csrf()
    deleted = move_content_to_trash("note", [note_id])
    flash("片段已移入回收站。" if deleted else "没有找到这条片段。", "success" if deleted else "error")
    return redirect(url_for("admin_notes"))


@app.post("/admin/notes/bulk-delete")
@admin_required
def admin_notes_bulk_delete():
    validate_csrf()
    selected = selected_content_ids()
    if not selected:
        flash("请先选择要删除的片段。", "error")
        return redirect(url_for("admin_notes"))
    deleted = move_content_to_trash("note", selected)
    flash(f"已将 {deleted} 条片段移入回收站。", "success")
    return redirect(url_for("admin_notes"))


@app.route("/admin/media", methods=["GET", "POST"])
@admin_required
def admin_media():
    if request.method == "POST":
        validate_csrf()
        inline_upload = request.headers.get("X-Orange-Upload") == "1"
        upload = request.files.get("file")
        original_name = Path(upload.filename or "").name if upload else ""
        extension = Path(original_name).suffix.lower().lstrip(".")
        if not upload or not original_name or extension not in ALLOWED_UPLOADS:
            if inline_upload:
                return {"ok": False, "message": "请选择 PNG、JPG、GIF 或 WebP 图片。"}, 422
            flash("请选择 PNG、JPG、GIF 或 WebP 图片。", "error")
        elif not (upload.mimetype or "").startswith("image/"):
            if inline_upload:
                return {"ok": False, "message": "上传文件不是有效图片。"}, 422
            flash("上传文件不是有效图片。", "error")
        else:
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            safe_stem = secure_filename(Path(original_name).stem)[:40] or "image"
            filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{safe_stem}-{secrets.token_hex(4)}.{extension}"
            target = UPLOAD_DIR / filename
            upload.save(target)
            get_db().execute(
                """
                INSERT INTO media (original_name, filename, mime_type, size, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (original_name[:255], filename, upload.mimetype or "", target.stat().st_size, now_iso()),
            )
            log_admin_action("上传图片", "media", original_name)
            get_db().commit()
            if inline_upload:
                image_url = url_for("uploaded_file", filename=filename)
                return {"ok": True, "url": image_url, "markdown": f"![截图]({image_url})"}, 201
            flash("图片已上传，可复制 Markdown 地址插入文章。", "success")
            return redirect(url_for("admin_media"))
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/media/delete/<int:media_id>")
@admin_required
def admin_media_delete(media_id: int):
    validate_csrf()
    deleted = move_content_to_trash("media", [media_id])
    flash("图片已移入回收站。" if deleted else "没有找到这张图片。", "success" if deleted else "error")
    return redirect(url_for("admin_media"))


@app.post("/admin/media/bulk-delete")
@admin_required
def admin_media_bulk_delete():
    validate_csrf()
    selected = selected_content_ids()
    if not selected:
        flash("请先选择要删除的图片。", "error")
        return redirect(url_for("admin_media"))
    deleted = move_content_to_trash("media", selected)
    flash(f"已将 {deleted} 张图片移入回收站。", "success")
    return redirect(url_for("admin_media"))


@app.get("/admin/revisions/<int:post_id>")
@admin_required
def admin_revisions(post_id: int):
    post = get_db().execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if post is None:
        abort(404)
    revisions = get_db().execute(
        "SELECT * FROM post_revisions WHERE post_id = ? ORDER BY id DESC", (post_id,)
    ).fetchall()
    return render_template("admin_revisions.html", post=post, revisions=revisions)


@app.post("/admin/revisions/restore/<int:revision_id>")
@admin_required
def admin_revision_restore(revision_id: int):
    validate_csrf()
    db = get_db()
    revision = db.execute("SELECT * FROM post_revisions WHERE id = ?", (revision_id,)).fetchone()
    if revision is None:
        abort(404)
    current = db.execute("SELECT * FROM posts WHERE id = ?", (revision["post_id"],)).fetchone()
    if current is None:
        abort(404)
    timestamp = now_iso()
    db.execute(
        "INSERT INTO post_revisions (post_id, title, excerpt, content, created_at, content_format) VALUES (?, ?, ?, ?, ?, ?)",
        (current["id"], current["title"], current["excerpt"], current["content"], timestamp, current['content_format']),
    )
    db.execute(
        "UPDATE posts SET title=?, excerpt=?, content=?, content_format=?, updated_at=? WHERE id=?",
        (revision["title"], revision["excerpt"], revision["content"], revision['content_format'], timestamp, current["id"]),
    )
    prune_post_revisions(db, current["id"])
    log_admin_action("恢复文章历史版本", "post", current["title"])
    db.commit()
    flash("已恢复所选历史版本，恢复前的内容也已保留。", "success")
    return redirect(url_for("admin_edit", post_id=current["id"]))


@app.post("/admin/toggle/<int:post_id>")
@admin_required
def admin_toggle(post_id: int):
    validate_csrf()
    db = get_db()
    post = db.execute(
        "SELECT title, status, published_at, content, content_format FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    if post is None:
        abort(404)
    new_status = "draft" if post["status"] == "published" else "published"
    if new_status == 'published' and post['content_format'] == 'latex':
        try:
            latex_bundle(post['content'])
        except LatexRenderError as error:
            flash(str(error) + ' 请先修正源码，或继续保留为草稿。', 'error')
            return redirect(url_for('admin_dashboard'))
    published_at = post["published_at"] or (now_iso() if new_status == "published" else "")
    db.execute(
        "UPDATE posts SET status = ?, published_at = ?, updated_at = ? WHERE id = ?",
        (new_status, published_at, now_iso(), post_id),
    )
    log_admin_action(
        "发布文章" if new_status == "published" else "转为草稿",
        "post",
        post["title"],
    )
    db.commit()
    flash("文章状态已更新。", "success")
    # Keep same-origin admin filters, but never redirect to a client-supplied host.
    referrer = request.referrer or ""
    try:
        parts = urlparse(referrer)
        if (parts.scheme in {"http", "https"} and parts.netloc == request.host
                and (parts.path == "/admin" or parts.path.startswith("/admin/"))
                and "\\" not in referrer and not any(ord(c) < 32 for c in referrer)):
            return redirect(parts.path + ("?" + parts.query if parts.query else ""))
    except ValueError:
        pass
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/delete/<int:post_id>")
@admin_required
def admin_delete(post_id: int):
    validate_csrf()
    deleted = move_content_to_trash("post", [post_id])
    flash("文章已移入回收站。" if deleted else "没有找到这篇文章。", "success" if deleted else "error")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/bulk-delete")
@admin_required
def admin_posts_bulk_delete():
    validate_csrf()
    selected = selected_content_ids()
    if not selected:
        flash("请先选择要删除的文章。", "error")
        return redirect(url_for("admin_dashboard"))
    deleted = move_content_to_trash("post", selected)
    flash(f"已将 {deleted} 篇文章移入回收站。", "success")
    return redirect(url_for("admin_dashboard"))


@app.get("/admin/trash")
@admin_required
def admin_trash():
    items = get_db().execute(
        "SELECT * FROM trash_items WHERE item_type != 'note' ORDER BY deleted_at DESC, id DESC"
    ).fetchall()
    counts = {
        row["item_type"]: row["count"]
        for row in get_db().execute(
            "SELECT item_type, COUNT(*) AS count FROM trash_items WHERE item_type != 'note' GROUP BY item_type"
        ).fetchall()
    }
    return render_template("admin_trash.html", items=items, counts=counts)


@app.post("/admin/trash/restore/<int:trash_id>")
@admin_required
def admin_trash_restore(trash_id: int):
    validate_csrf()
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    item = db.execute("SELECT * FROM trash_items WHERE id = ?", (trash_id,)).fetchone()
    if item is None:
        abort(404)
    config = TRASH_TYPES[item["item_type"]]
    payload = json.loads(item["payload"])
    row_data = {column: payload.get(column) for column in config["columns"]}
    if item['item_type'] == 'post' and not row_data.get('content_format'):
        row_data['content_format'] = 'markdown'
    columns = list(row_data)
    try:
        db.execute(
            f"INSERT INTO {config['table']} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            [row_data[column] for column in columns],
        )
        if item["item_type"] == "post":
            for revision in payload.get("__revisions", []):
                db.execute(
                    """
                    INSERT INTO post_revisions
                    (post_id, title, excerpt, content, created_at, content_format)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row_data["id"],
                        revision.get("title", ""),
                        revision.get("excerpt", ""),
                        revision.get("content", ""),
                        revision.get("created_at", now_iso()),
                        revision.get('content_format', 'markdown'),
                    ),
                )
            prune_post_revisions(db, row_data["id"])
            restore_post_community(db, row_data["id"], payload.get("__community", {}))
            if row_data.get("is_featured"):
                db.execute(
                    "UPDATE posts SET is_featured = 0 WHERE id != ?", (row_data["id"],)
                )
        if item["item_type"] == "project":
            restore_project_community(db, row_data["id"], payload.get("__community", {}))
        db.execute("DELETE FROM trash_items WHERE id = ?", (trash_id,))
        log_admin_action("从回收站恢复", item["item_type"], item["title"])
        db.commit()
    except (sqlite3.IntegrityError, sqlite3.OperationalError):
        db.rollback()
        flash("恢复失败：原位置已有同名链接或数据结构发生变化。", "error")
        return redirect(url_for("admin_trash"))
    flash(f"《{item['title']}》已恢复。", "success")
    return redirect(url_for("admin_trash"))


@app.post("/admin/trash/delete/<int:trash_id>")
@admin_required
def admin_trash_delete(trash_id: int):
    validate_csrf()
    db = get_db()
    item = db.execute("SELECT * FROM trash_items WHERE id = ?", (trash_id,)).fetchone()
    if item is None:
        abort(404)
    if item["item_type"] == "media" and not delete_trashed_media_file(
        json.loads(item["payload"])
    ):
        flash("图片文件暂时无法清理，请稍后重试。", "error")
        return redirect(url_for("admin_trash"))
    db.execute("DELETE FROM trash_items WHERE id = ?", (trash_id,))
    log_admin_action("彻底删除", item["item_type"], item["title"])
    db.commit()
    flash("内容已彻底删除，无法再恢复。", "success")
    return redirect(url_for("admin_trash"))


@app.post("/admin/trash/empty")
@admin_required
def admin_trash_empty():
    validate_csrf()
    db = get_db()
    items = db.execute("SELECT * FROM trash_items ORDER BY id").fetchall()
    removable: list[int] = []
    failed = 0
    for item in items:
        if item["item_type"] == "media" and not delete_trashed_media_file(
            json.loads(item["payload"])
        ):
            failed += 1
            continue
        removable.append(item["id"])
    if removable:
        placeholders = ",".join("?" for _ in removable)
        db.execute(f"DELETE FROM trash_items WHERE id IN ({placeholders})", removable)
        log_admin_action("清空回收站", "system", "", f"彻底删除 {len(removable)} 项")
        db.commit()
    if failed:
        flash(f"已清理 {len(removable)} 项，{failed} 个图片文件暂时无法删除。", "error")
    else:
        flash(f"回收站已清空，共彻底删除 {len(removable)} 项。", "success")
    return redirect(url_for("admin_trash"))




# Redefine theme integration; public content only.
@app.template_filter("cover_image")
def theme_cover_image(content):
    match = re.search(r"!\[[^\]]*\]\(([^\s)]+)", str(content or ""))
    if not match:
        return ""
    candidate = match.group(1)
    return candidate if candidate.startswith("/") and not candidate.startswith("//") or candidate.startswith("https://") else ""


@app.context_processor
def theme_context():
    if request.path.startswith("/admin") and request.endpoint not in {"admin_preview", "admin_project_preview"}:
        return {}
    db = get_db()
    rows = db.execute("SELECT category,tags FROM posts WHERE status='published'").fetchall()
    return {"theme_stats": {"posts":len(rows), "categories":len({r["category"] for r in rows}), "tags":len({tag for r in rows for tag in split_tags(r["tags"])})}}


@app.get("/tags")
@app.get("/categories")
def theme_taxonomy():
    track_pageview(request.path)
    rows = get_db().execute("SELECT category,tags FROM posts WHERE status='published'").fetchall()
    cats, tags = {}, {}
    for row in rows:
        cats[row["category"]] = cats.get(row["category"],0)+1
        for tag in split_tags(row["tags"]):
            tags[tag] = tags.get(tag,0)+1
    return render_template("taxonomy.html",taxonomy=request.path.strip("/"),topic_tags=sorted(tags.items()),topic_categories=[{"category":k,"count":v} for k,v in sorted(cats.items())])


@app.get("/tags/<path:tag>")
def theme_tag(tag):
    return redirect(url_for("articles",tag=tag))


@app.get("/categories/<path:category>")
def theme_category(category):
    return redirect(url_for("articles",category=category))


@app.get("/archives")
@app.get("/archives/")
def theme_archives():
    return archive()


@app.get("/search-index.json")
def theme_search_index():
    raw = request.args.get('after', '0')
    if not raw.isascii() or not raw.isdigit() or len(raw) > 18:
        abort(400)
    rows = get_db().execute("SELECT id,title,slug,excerpt,content FROM posts WHERE status='published' AND id>? ORDER BY id LIMIT 21", (int(raw),)).fetchall()
    response = jsonify([{"title":r["title"],"url":url_for("post_detail",slug=r["slug"]),"excerpt":r["excerpt"],"content":r["content"]} for r in rows[:20]])
    if len(rows) > 20:
        response.headers['X-Next-Cursor'] = str(rows[19]['id'])
    return response



@app.before_request
def disable_retired_notes():
    if request.path.rstrip('/') == '/notes' or request.path.startswith('/notes/') or request.path.rstrip('/') == '/admin/notes' or request.path.startswith('/admin/notes/'):
        abort(404)


@app.after_request
def security_response_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # Existing pages use inline scripts/styles: this policy only constrains
    # embedding, document bases and plugin objects, not general script sources.
    response.headers.setdefault("Content-Security-Policy",
                                "object-src 'none'; base-uri 'self'; frame-ancestors 'self'")
    if request.path == "/admin" or request.path.startswith("/admin/"):
        response.headers["Cache-Control"] = "private, no-store"
        response.vary.add("Cookie")
    return response


install_community(app, get_db, now_iso, visitor_fingerprint, classify_client)

from latex_workbench import install_latex_workbench, project_workspace_id
install_latex_workbench(app, get_db, admin_required, validate_csrf, now_iso, DB_PATH, UPLOAD_DIR)


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(host="127.0.0.1", port=8000, debug=False)
