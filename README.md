# Orange Blog

Orange Blog 是基于 Flask 和 SQLite 的个人博客，包含文章与项目展示、后台编辑、读者账号、点赞评论、访问统计及 LaTeX 文档工作区。前端使用 Jinja 模板、CSS 和原生 JavaScript。

本仓库以 2026-10-23 核对的线上源码为初始基线。它保存应用源码及必要静态资源；文章、账号、评论、上传文件和真实运行配置由独立数据存储提供。

## 技术栈

- Python 3.11+、Flask 3.x、Werkzeug、Jinja2。
- SQLite：内容、设置、读者互动及会话等数据。
- Python-Markdown、Bleach：Markdown 渲染和 HTML 清理。
- Pillow：头像等图像处理，版本见 `requirements-profile.txt`。
- 原生 JavaScript、CSS、本地字体、PDF.js 等静态资源。
- Linux 运行环境可使用 Gunicorn；完整 LaTeX 功能额外依赖 Pandoc、TeX 和 bubblewrap。

## 目录结构

```text
orange-blog/
├── app.py                    # Flask 入口、文章/项目管理、数据库初始化
├── community.py              # 读者认证、文章互动
├── project_community.py      # 项目互动
├── accounts.py / owner.py    # 账号管理与博主身份
├── reader_profile.py         # 读者个人资料
├── moderation.py             # 评论审核和反垃圾
├── auth_security.py          # 会话、登录限流等
├── runtime_security.py       # 密钥与 Host 配置验证
├── latex_renderer.py         # LaTeX 转 HTML
├── latex_workbench.py        # LaTeX 项目及编译工作区
├── project_web.py            # 项目内容渲染辅助
├── templates/               # Jinja 页面与组件
├── static/                  # CSS、JS、图片、字体、PDF.js
├── migrations/              # SQL 迁移脚本，属于源码
├── deploy/                  # systemd 维护任务模板
├── requirements.txt         # 核心 Python 依赖
├── requirements-profile.txt # 图像处理依赖
├── .env.example             # 环境变量示例，不自动加载
└── SOURCE_BASELINE.md        # 初始源码核对范围
```

`security_admin.py`、`auth_maintenance.py`、`migrate_auth.py`、`migrate_community.py`、`ticket_handoff.py`、`cleanup-trash.py` 是维护或迁移入口；普通本地试运行不需要执行它们。

## 安装依赖

以下命令用于 Windows PowerShell，在仓库根目录执行。虚拟环境放在本机；不需要管理员权限，也不需要连接生产服务器。

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-profile.txt
```

无需激活虚拟环境，后续直接调用其 Python。Gunicorn 依赖声明保留线上版本；Windows 本地使用 Flask 开发服务器。

## 本地运行

每次新开 PowerShell 时设置本地环境变量。下面生成全新的随机密钥，并交互输入本地管理员密码；不要使用生产密钥、密码或数据库。

```powershell
$env:ORANGE_SECRET_KEY = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
$env:ORANGE_ADMIN_USER = 'local-admin'
$env:ORANGE_ADMIN_PASSWORD_HASH = & .\.venv\Scripts\python.exe -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass('Local admin password: ')))"
$env:ORANGE_DB = Join-Path $PWD 'data\local-blog.db'
$env:ORANGE_UPLOADS = Join-Path $PWD 'data\uploads'
$env:ORANGE_TRUSTED_HOSTS = 'localhost,127.0.0.1'
& .\.venv\Scripts\python.exe -c "import app; app.app.config['SESSION_COOKIE_SECURE'] = False; app.app.run(host='127.0.0.1', port=8000, debug=False)"
```

打开 <http://127.0.0.1:8000/>，后台入口为 <http://127.0.0.1:8000/admin/login>。首次请求会在上面指定的本地路径初始化数据库并填入应用内置示例内容。管理员账号是 `local-admin`，密码为刚才输入的本地密码。按 `Ctrl+C` 停止服务。

`SESSION_COOKIE_SECURE=False` 仅在这次本地 HTTP 进程中生效，方便浏览器保存登录会话；不要用于生产 HTTPS 配置。不要直接把示例密码占位符当作密码哈希。

应用不自动读取 `.env` 或 `.env.example`；复制文件不会设置环境变量。`ORANGE_SECRET_KEY` 必须显式提供至少 32 字节，缺失时应用拒绝启动。每次随机生成密钥会使旧会话失效。

## 可选功能与数据边界

- Markdown 页面和常规账号/编辑流程可在本地验证。LaTeX 转 HTML 需把 `ORANGE_PANDOC` 设为实际 Pandoc 路径；完整 PDF 编译需额外准备 Linux、TeX、bubblewrap 和字体环境，不能仅凭 pip 安装完成。
- `static/vendor/pdfjs/` 的 worker、cMaps、字体及 wasm 等是浏览器所需资源，应整体保留。
- `toolbox-downloads/` 中的三个安装包不进入 Git；未提供文件时工具箱列表可显示，实际下载会返回 404。
- `static/redefine/codex-activity.json` 是运行时生成的活动数据，不进入 Git；缺少时热力图不能加载真实记录。
- 数据库、用户上传和 LaTeX 工作区应单独备份恢复。此仓库不是现有网站文章、用户和图片的完整备份。

## 提交与部署

`.gitignore` 排除真实环境配置、常见密钥、数据库及其 WAL/SHM、上传目录、日志、缓存、虚拟环境、备份、安装包和临时文件。它不能识别所有未知命名的秘密；每次提交前仍需检查文件清单和差异。

```powershell
git status --short
git diff --cached --stat
git diff --cached --name-only
```

本仓库暂不包含自动部署。GitHub 推送不会自动修改线上服务。`deploy/` 仅是维护任务模板，不是完整生产环境配置；配置生产环境或迁移既有数据应另行审查。

`README_读者互动.md` 和 `AUTH_SECURITY.md` 保留原有专题记录，其中的历史部署命令与旧限制不一定反映所有后续改动；当前行为以源码和本 README 的启动说明为准。
