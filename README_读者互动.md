# Orange 博客：读者账户、浏览量、点赞与评论

本目录来自部署前 `/opt/orange-blog/current` 的完整源码快照，在现有 Flask 项目内修改。不是新建网站。数据库继续使用 SQLite；不需要 MySQL、Redis、邮件、短信或其他第三方服务。

## 原项目结构与修改方案

- `app.py`：Flask 路由、SQLite 初始化、文章管理与分析；原 `posts.views` 已有历史计数。本次复用此字段，不清零、不替换数据。
- `templates/`：Jinja 页面，前台继承 `redefine_base.html`，文章列表共用 `_redefine_cards.html`，详情为 `post.html`。
- `static/redefine/`：Redefine 主题、原生 JavaScript 与现有 PJAX 导航。
- `latex_renderer.py`：原 LaTeX 文章渲染；`static/admin*` 与 `templates/admin*`：原后台界面，本次保留。
- 生产：Gunicorn + systemd `orange-blog`，入口 `app:app`，数据库 `/var/lib/orange-blog/blog.db`，上传文件 `/var/lib/orange-blog/uploads`。

新增模块 `community.py` 在原应用内注册 Blueprint，账户与原管理员会话分开。原文章、后台样式、编辑器快捷保存、四空格正文及每篇最近 5 份历史版本均保留。

## 修改/新增文件

| 文件 | 用途 |
|---|---|
| `app.py` | 初始化新增表，接入浏览量去重/详情数据，回收站保存和恢复互动数据 |
| `community.py` | 读者认证、点赞、评论、访问去重、校验及频率限制 |
| `migrations/20261019_reader_community.sql` | 可重复执行的增量结构迁移 |
| `migrate_community.py` | 迁移 CLI：先在线备份，再在事务中迁移并检查文章未变 |
| `templates/redefine_base.html` | 桌面/手机登录状态入口、登录提示框及新资源 |
| `templates/_redefine_cards.html` | “阅读全文”旁显示真实浏览量 |
| `templates/post.html` | 正式文章详情底部加入互动区，后台预览不显示互动区 |
| `templates/_reader_account.html` | 当前昵称、登录/注册、退出入口 |
| `templates/reader_auth.html` | 注册/登录表单、失败提示、原文章回跳 |
| `templates/_post_community.html` | 点赞、评论输入和列表、分页 |
| `static/redefine/community.css` | 复用主题变量，适配桌面、手机、深浅色 |
| `static/redefine/community.js` | 点赞/取消、评论提交、登录提示、跨页状态同步 |
| `static/redefine/orange-navigation.js` | 兼容互动脚本的 PJAX，认证页正常整页导航 |

## 数据库

原字段 `posts.views` 保持原值。新增 5 张表：

| 表 | 主要字段与约束 |
|---|---|
| `reader_users` | id、nickname、nickname_key UNIQUE、password_hash、created_at |
| `post_likes` | post_id、user_id、created_at；联合主键确保每用户每文章最多一个赞 |
| `post_comments` | id、post_id、user_id、content、created_at、submission_id；用户+提交标识唯一 |
| `post_view_visitors` | post_id、visitor_key、last_counted_at；文章+身份联合主键 |
| `reader_rate_limits` | rate_key、window_start、attempts；数据库持久化的认证限流 |

所有互动通过外键关联文章/用户；移动文章到回收站时保存互动数据，恢复时重新关联。用户昵称按 NFKC + casefold 判断唯一，保留显示昵称；密码使用 Werkzeug scrypt 哈希。登录、评论、点赞由后端验证会话及 CSRF，普通读者不能访问管理后台。读者登录/退出不清除已有管理员登录状态。

浏览量在同一浏览器/已登录账户的 30 分钟窗口内只增加一次；窗口按上次有效计数计算。识别到的爬虫及 HEAD 请求不累计文章浏览量。清除 Cookie、改用不同设备等情况下无法保证完全去重；这不是实名访客统计。原后台访客分析保留自身原有统计口径。

评论 1–2000 字、按时间倒序显示，每页 20 条。评论作为纯文本显示，HTML 不执行；10 秒内限制重复发新评论；相同提交标识重试不重复插入。注册/登录有按来源的数据库限流。昵称 1–24 字，密码 8–128 字。读者会话沿用现有 12 小时有效期及活动续期，生产 Cookie 保持 Secure/HttpOnly/SameSite=Lax。

## 接口

| 方法/路径 | 说明 |
|---|---|
| GET/POST `/auth/register` | nickname、password、confirm_password、csrf_token、next |
| GET/POST `/auth/login` | nickname、password、csrf_token、next |
| POST `/auth/logout` | csrf_token、next；安全地限制回跳到本站公开路径 |
| GET `/api/reader/session` | 返回后端核验的当前读者和互动 CSRF token |
| POST `/api/posts/<id>/like` | liked=1 设置点赞，liked=0 取消；幂等，需登录及 CSRF |
| GET `/api/posts/<id>/comments?comments_page=1` | 所有人可查看已发布文章评论 |
| POST `/api/posts/<id>/comments` | content、submission_id（32 位十六进制）、csrf_token；需登录 |

提交使用表单数据。异步请求带 `Accept: application/json`；点赞/评论返回数据库实际结果。401 未登录、400 CSRF 失效、404 未发布/不存在、409 昵称冲突/重试冲突、422 参数错误、429 频率限制、503 数据库暂忙。普通表单成功后 303 跳回文章。账户相关及动态 HTML 禁止共享缓存。

## 迁移、启动

生产沿用当前配置，无需增加环境变量。部署脚本先备份源文件和数据库、校验旧哈希，再执行：

```sh
/opt/orange-blog/current/.venv/bin/python /opt/orange-blog/current/migrate_community.py \
  --database /var/lib/orange-blog/blog.db \
  --backup /var/backups/orange-blog/本次备份目录/before-migration.db
systemctl restart orange-blog
systemctl status orange-blog
```

迁移可以重复执行；备份文件名必须使用新路径，避免覆盖原备份。原应用初始化也会幂等检查新增表。已有 `ORANGE_SECRET_KEY`、`ORANGE_DB`、`ORANGE_UPLOADS`、管理员配置和服务保持不变。

Windows 本地启动（Python 3.11+）：

```powershell
cd 'C:\Users\成子鸣\Desktop\wangzhan\work\community'
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:ORANGE_DB = Join-Path $PWD 'data\local.db'
.\.venv\Scripts\python -c "from app import app; app.config['SESSION_COOKIE_SECURE']=False; app.run(host='127.0.0.1',port=8000)"
```

浏览器打开 `http://127.0.0.1:8000/`。关闭 Secure Cookie 仅用于本机 HTTP 启动命令，生产使用原 `gunicorn app:app`，仍强制 Secure Cookie。本地数据库是独立的；需要稳定跨重启本地登录时，自行配置固定 `ORANGE_SECRET_KEY`。原有 LaTeX 功能仍依赖项目既有的 Pandoc 运行环境，新互动功能无需 Pandoc。

## 测试及边界

完整写入测试在服务器独立副本与独立 SQLite 数据库运行，无模拟数据，也不向生产带入测试账户/评论。

- 迁移两次后原文章及旧 views 不变；外键检查通过。
- 昵称唯一、注册/登录/退出、哈希存储、密码错误、CSRF、跳转校验、限流、普通读者无法访问后台。
- 浏览量：保留基数、刷新去重、30 分钟到期重新计数、不同浏览器、爬虫/HEAD、已登录账户跨浏览器、匿名转登录。
- 点赞/取消、刷新状态、重复提交、两用户计数、身份不能由表单 user_id 冒充。
- 评论登录保护、刷新持久化、昵称/内容/时间、分页排序、HTML 转义、超长/空评论、重复提交及频率限制。
- 同一用户 12 次并发访问只增加一次；12 次并发点赞只保存一条。
- Edge 真实操作：匿名提示→注册→回到原文→点赞/取消→评论→刷新→退出→登录；PJAX 切换正常。
- 390/768/820/1024/1440px 布局及深色主题；登录/注册、文章列表及评论区无横向溢出。
- 原后台普通保存、Ctrl+S、刷新、预览正常；预览不产生读者互动或浏览量。回收站删除/恢复保留互动数据。

测试脚本 `qa_community.py` 只用于隔离副本，**不在生产发布包中**。浏览器/并发测试脚本位于上一级工作目录。部署和生产验证结果另见上级 `读者互动上线记录.md`。
