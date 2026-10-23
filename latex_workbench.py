"""Private administrator LaTeX projects. Source stays in SQLite; builds are isolated."""
from __future__ import annotations
import base64, hashlib, hmac, io, json, os, re, secrets, shutil, signal, sqlite3, subprocess, tempfile, threading, time, zipfile
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone
from project_web import workspace_article_source
from latex_renderer import LatexRenderError
from flask import Blueprint, abort, jsonify, redirect, render_template, request, send_file, session, url_for

TEXT_EXT = {'.tex', '.bib', '.sty', '.cls', '.bst', '.bbx', '.cbx', '.def', '.cfg', '.clo', '.fd', '.map', '.enc', '.ist', '.tikz', '.csv', '.txt', '.md'}
BINARY_EXT = {'.png', '.jpg', '.jpeg', '.pdf', '.eps', '.svg', '.ttf', '.otf'}
MAX_FILE = 4 * 1024 * 1024
MAX_PROJECT = 25 * 1024 * 1024
MAX_FILES = 120
TEMPLATE = r'''\documentclass[UTF8,a4paper,12pt]{ctexart}
\usepackage{amsmath,amssymb,graphicx,geometry,hyperref}
\geometry{margin=2.5cm}
\title{我的 LaTeX 文档}
\author{Orange}
\date{\today}
\begin{document}
\maketitle
\tableofcontents
\section{开始写作}
这是你的私人 LaTeX 工作台。左侧编辑源码，右侧查看 PDF。

行内公式 $E=mc^2$，独立公式：
\[
  \int_0^1 x^2\,dx=\frac{1}{3}.
\]
\subsection{多文件与图片}
可在文件列表中新建章节，通过 \verb|\input{chapters/intro}| 引入。
上传图片后，使用 \verb|\includegraphics[width=.8\textwidth]{image.png}|。
\end{document}
'''
ENGLISH_TEMPLATE = r'''\documentclass[a4paper,12pt]{article}
\usepackage{amsmath,amssymb,graphicx,geometry,hyperref}
\geometry{margin=2.5cm}
\title{My LaTeX document}
\author{Orange}
\date{\today}
\begin{document}
\maketitle
\section{Introduction}
Write here. $E=mc^2$.
\end{document}
'''

class WorkbenchError(ValueError):
    def __init__(self, message, status=422):
        self.status = status
        super().__init__(message)

def valid_path(value):
    if not isinstance(value, str) or len(value) > 180 or not value or '\\' in value or ':' in value or '\x00' in value:
        raise WorkbenchError('文件路径无效。')
    p = PurePosixPath(value)
    if p.is_absolute() or any(part in {'', '.', '..'} or part.startswith('.') or not re.fullmatch(r'[\w .+@()-]+', part, re.UNICODE) for part in value.split('/')):
        raise WorkbenchError('文件名仅支持文字、数字、空格和 _- .+@()，不能使用隐藏路径或上级目录。')
    if p.suffix.lower() not in TEXT_EXT | BINARY_EXT:
        raise WorkbenchError('不支持这种文件格式：' + p.suffix)
    return value

def validate_files(files):
    if not files or len(files) > MAX_FILES:
        raise WorkbenchError('项目需要包含文件，且不能超过 120 个文件。')
    if sum(len(v) for v in files.values()) > MAX_PROJECT:
        raise WorkbenchError('项目文件总大小不能超过 25 MB。')
    folded = set()
    for name, data in files.items():
        valid_path(name)
        if len(data) > MAX_FILE:
            raise WorkbenchError('单个文件不能超过 4 MB：' + name)
        if name.casefold() in folded:
            raise WorkbenchError('文件名不能仅通过大小写区分。')
        folded.add(name.casefold())
        if PurePosixPath(name).suffix.lower() in TEXT_EXT:
            try: data.decode('utf-8-sig')
            except UnicodeDecodeError: raise WorkbenchError('文本文件请使用 UTF-8 编码：' + name)

def schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS latex_projects(
      id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, main_file TEXT NOT NULL DEFAULT 'main.tex',
      engine TEXT NOT NULL DEFAULT 'xelatex', version INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS latex_files(
      project_id INTEGER NOT NULL REFERENCES latex_projects(id) ON DELETE CASCADE,
      path TEXT NOT NULL, data BLOB NOT NULL, PRIMARY KEY(project_id,path));
    CREATE TABLE IF NOT EXISTS latex_revisions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES latex_projects(id) ON DELETE CASCADE,
      path TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS latex_builds(
      id TEXT PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES latex_projects(id) ON DELETE CASCADE,
      version INTEGER NOT NULL, status TEXT NOT NULL, log TEXT NOT NULL DEFAULT '', errors TEXT NOT NULL DEFAULT '[]',
      created_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '');
    CREATE INDEX IF NOT EXISTS latex_build_project ON latex_builds(project_id,created_at);
    ''')

def errors_from_log(log, main_file):
    errors = []
    for line in log.splitlines():
        match = re.match(r'(?:\./|/work/)?([^:\n]+\.tex):(\d+):\s*(.+)', line)
        if match:
            path, number, message = match.groups()
            errors.append({'file': path, 'line': int(number), 'message': message[:500]})
        elif line.startswith('!'):
            errors.append({'file': main_file, 'line': 1, 'message': line[1:].strip()[:500]})
        elif re.match(r'l\.\d+', line) and errors:
            errors[-1]['line'] = int(re.match(r'l\.(\d+)', line).group(1))
    return errors[:30]

def sandbox_command(work, engine, main, bib=False):
    """Mount compiler/runtime and this snapshot only. No website, home, network or secrets."""
    bwrap = shutil.which('bwrap')
    runtime = Path(os.environ.get('ORANGE_TEX_ROOT', '/opt/orange-blog/tools/latex-texmf'))
    binary = Path(shutil.which('bibtex' if bib else engine) or '/nonexistent')
    if not bwrap or not binary.is_file() or not runtime.is_dir():
        raise WorkbenchError('PDF 编译环境尚未就绪，请联系站点管理员。', 503)
    command = [shutil.which('prlimit') or '/usr/bin/prlimit', '--as=805306368', '--cpu=35', '--fsize=33554432', '--nofile=128', '--nproc=64', '--', bwrap,
               '--unshare-all', '--die-with-parent', '--new-session', '--cap-drop', 'ALL']
    for name in ['/usr', '/lib', '/lib64', '/bin']:
        if Path(name).exists(): command += ['--ro-bind', name, name]
    command += ['--ro-bind', str(runtime), '/texmf/orange', '--ro-bind', str(Path('/usr/share/texlive/texmf-dist').resolve()), '/texmf/dist']
    for origin,target in [('/usr/share/texlive/texmf-var','/texmf/sysvar'),('/usr/share/texlive/texmf-config','/texmf/sysconfig')]:
        if Path(origin).exists():command += ['--ro-bind',str(Path(origin).resolve()),target]
        else:command += ['--dir',target]
    for name in ['/etc/fonts', '/etc/ld.so.cache', '/etc/texlive', '/var/lib/texmf', '/var/cache/fontconfig']:
        if Path(name).exists(): command += ['--ro-bind', name, name]
    cache = work/'.tex-cache'
    command += ['--bind', str(cache), '/texmf/cache'] if cache.is_dir() else ['--tmpfs','/texmf/cache']
    command += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/home',
                '--bind', str(work), '/work', '--chdir', '/work',
                '--setenv', 'PATH', str(binary.parent)+':/usr/bin:/bin', '--setenv', 'HOME', '/tmp',
                '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'TEXMFHOME', '/texmf/orange', '--setenv', 'TEXMFROOT', '/usr/share/texlive', '--setenv', 'TEXMFDIST', '/texmf/dist', '--setenv', 'TEXMFSYSVAR', '/texmf/sysvar', '--setenv', 'TEXMFSYSCONFIG', '/texmf/sysconfig', '--setenv', 'TEXMFLOCAL', '/texmf/local', '--setenv', 'TEXMFOUTPUT', '/texmf', '--setenv', 'TEXMFVAR', '/texmf/cache/var',
                '--setenv', 'TEXMFCONFIG', '/texmf/cache/config', '--setenv', 'openin_any', 'p',
                '--setenv', 'openout_any', 'p', '--setenv', 'shell_escape', 'f', str(binary)]
    if bib: command += ['./'+str(PurePosixPath(main).with_suffix(''))]
    else: command += ['-no-shell-escape', '-halt-on-error', '-interaction=nonstopmode', '-file-line-error', '-synctex=1', '-jobname=output', './'+main]
    return command

def run_process(command, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0: raise WorkbenchError('编译超过 90 秒，已停止。')
    # Output goes to a bounded temporary file rather than unbounded subprocess pipes.
    with tempfile.TemporaryFile() as output:
        proc = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, start_new_session=True, env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
        try: proc.wait(timeout=min(45, remaining))
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL);proc.wait()
            raise WorkbenchError('编译超时，已停止。请检查循环命令或缩小项目。')
        output.seek(0)
        return proc.returncode, output.read(256*1024).decode('utf-8', errors='replace')

def compile_snapshot(work, engine, main):
    (work/'.tex-cache').mkdir(parents=True, exist_ok=True, mode=0o700)
    deadline = time.monotonic() + 90
    logs = []
    bib_required = False
    def navigation_files():
        return {str(p.relative_to(work)):p.read_bytes() for p in work.rglob('*') if p.suffix in {'.toc','.lof','.lot','.out'} and p.is_file() and p.stat().st_size}
    for iteration in range(3):
        previous_navigation = navigation_files()
        code, log = run_process(sandbox_command(work, engine, main), deadline)
        logs.append(log)
        if code: return False, '\n'.join(logs)
        bibliography = False
        if iteration == 0:
            aux = work/'output.aux'
            if aux.exists() and '\\bibdata' in aux.read_text(errors='replace'):
                bibliography = True
                bib_required = True
                code, log = run_process(sandbox_command(work, engine, 'output', bib=True), deadline)
                logs.append(log)
                if code: return False, '\n'.join(logs)
        # Repeat only when TeX requests it, or after BibTeX has changed references.
        rerun = previous_navigation != navigation_files() or bibliography or (bib_required and iteration < 2) or bool(re.search(r'Rerun to get|Label\(s\) may have changed|rerunfilecheck Warning|Please (?:re)?run', log, re.I))
        if not rerun: break
    pdf = work/'output.pdf'
    return pdf.is_file() and pdf.read_bytes().startswith(b'%PDF-'), '\n'.join(logs)

def project_workspace_id(db, site_id):
    if site_id is None:return None
    row=db.execute('SELECT value FROM settings WHERE key=?',('latex.workspace.project.'+str(site_id),)).fetchone()
    if not row or not row['value'].isdigit():return None
    wid=int(row['value'])
    return wid if db.execute('SELECT 1 FROM latex_projects WHERE id=?',(wid,)).fetchone() else None

def workspace_site_id(db, wid):
    rows=db.execute("SELECT key FROM settings WHERE key LIKE 'latex.workspace.project.%' AND value=?",(str(wid),)).fetchall()
    for row in rows:
        suffix=row['key'].rsplit('.',1)[-1]
        if suffix.isdigit() and db.execute('SELECT 1 FROM projects WHERE id=?',(int(suffix),)).fetchone():return int(suffix)
    return None

def install_latex_workbench(app, get_db, admin_required, validate_csrf, now_iso, db_path, upload_dir):
    bp = Blueprint('latex_workbench', __name__, url_prefix='/admin/latex')
    storage = Path(db_path).parent/'latex-workbench'
    schema_lock = threading.Lock()
    ready = False

    @bp.before_request
    def gate():
        nonlocal ready
        result = admin_required(lambda: None)()
        if result is not None: return result
        request.max_content_length=30*1024*1024
        if request.method != 'GET':
            if request.is_json:
                expected=session.get('csrf_token','');received=request.headers.get('X-CSRF-Token','')
                if not expected or not hmac.compare_digest(expected.encode(),received.encode()):abort(400)
            else:validate_csrf()
        with schema_lock:
            if not ready:
                schema(get_db());ready = True
        storage.mkdir(parents=True, exist_ok=True, mode=0o700)

    @bp.errorhandler(WorkbenchError)
    def error(exc): return jsonify(ok=False, message=str(exc)), exc.status

    @bp.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    def json_body():
        body=request.get_json(silent=True)
        if not isinstance(body,dict):raise WorkbenchError('请求数据无效。',400)
        return body

    def project(pid):
        row = get_db().execute('SELECT * FROM latex_projects WHERE id=?', (pid,)).fetchone()
        if row is None: abort(404)
        return dict(row)

    def file_map(pid):
        return {r['path']: bytes(r['data']) for r in get_db().execute('SELECT path,data FROM latex_files WHERE project_id=? ORDER BY path', (pid,))}

    def checked_write(pid, version):
        db = get_db()
        db.execute('BEGIN IMMEDIATE')
        p = project(pid)
        if version != p['version']:
            db.rollback()
            raise WorkbenchError('另一个标签页已经更新这个项目。当前内容已保留在本机，请下载备份后刷新。', 409)
        return db, p

    def remember(db, pid, name, data):
        if PurePosixPath(name).suffix.lower() not in TEXT_EXT: return
        db.execute('INSERT INTO latex_revisions(project_id,path,content,created_at) VALUES(?,?,?,?)', (pid,name,data.decode('utf-8-sig'),now_iso()))
        db.execute('DELETE FROM latex_revisions WHERE project_id=? AND path=? AND id NOT IN (SELECT id FROM latex_revisions WHERE project_id=? AND path=? ORDER BY id DESC LIMIT 30)',(pid,name,pid,name))

        # Bound aggregate history, including histories for removed files.
        sizes=db.execute('SELECT id,length(CAST(content AS BLOB)) AS bytes FROM latex_revisions WHERE project_id=? ORDER BY id DESC',(pid,)).fetchall()
        total=0
        for index,row in enumerate(sizes):
            total+=row['bytes']
            if index>=300 or total>64*1024*1024:
                db.execute('DELETE FROM latex_revisions WHERE id=?',(row['id'],))

    def updated(db, pid, p, title=None, main=None, engine=None):
        db.execute('UPDATE latex_projects SET title=?,main_file=?,engine=?,version=version+1,updated_at=? WHERE id=?',
                   (title if title is not None else p['title'],main if main is not None else p['main_file'],engine if engine is not None else p['engine'],now_iso(),pid))
        db.commit()
        return jsonify(ok=True, version=p['version']+1)

    @bp.get('')
    @bp.get('/')
    def index():
        items = get_db().execute('SELECT p.*, (SELECT COUNT(*) FROM latex_files f WHERE f.project_id=p.id) AS file_count FROM latex_projects p ORDER BY updated_at DESC,id DESC').fetchall()
        return render_template('admin_latex_projects.html', projects=items)

    @bp.post('/new')
    def create():
        title = request.form.get('title', '').strip()[:120] or '未命名 LaTeX 项目'
        content = ENGLISH_TEMPLATE if request.form.get('template') == 'english' else TEMPLATE
        db = get_db();stamp = now_iso()
        pid = db.execute('INSERT INTO latex_projects(title,created_at,updated_at) VALUES(?,?,?)',(title,stamp,stamp)).lastrowid
        db.execute('INSERT INTO latex_files(project_id,path,data) VALUES(?,?,?)',(pid,'main.tex',content.encode()))
        db.commit()
        return redirect(url_for('latex_workbench.editor', pid=pid))

    @bp.post('/editor-format/<int:site_id>')
    def editor_format(site_id):
        body=json_body();mode=body.get('format')
        if not isinstance(mode,str) or mode not in {'markdown','latex'}:raise WorkbenchError('编辑格式无效。',422)
        db=get_db()
        if not db.execute('SELECT 1 FROM projects WHERE id=?',(site_id,)).fetchone():abort(404)
        db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('latex.editor.format.project.'+str(site_id),mode))
        db.commit()
        return jsonify(ok=True,format=mode)

    @bp.post('/from-project/<int:site_id>')
    def from_project(site_id):
        body=json_body();source=body.get('initial_content','')
        if not isinstance(source,str) or len(source.encode())>MAX_FILE:raise WorkbenchError('初始源码过大。')
        db=get_db();db.execute('BEGIN IMMEDIATE')
        site=db.execute('SELECT * FROM projects WHERE id=?',(site_id,)).fetchone()
        if site is None:abort(404)
        wid=project_workspace_id(db,site_id)
        if wid is None:
            source=source if source.strip() else site['content']
            if not source.strip():source=TEMPLATE
            elif not re.search(r'\\(?:documentclass|begin\s*\{document\})',source):
                source='\\documentclass[UTF8]{ctexart}\n\\usepackage{amsmath,amssymb,graphicx,hyperref}\n\\begin{document}\n'+source+'\n\\end{document}\n'
            validate_files({'main.tex':source.encode()})
            stamp=now_iso()
            wid=db.execute('INSERT INTO latex_projects(title,created_at,updated_at) VALUES(?,?,?)',(site['title'],stamp,stamp)).lastrowid
            db.execute('INSERT INTO latex_files(project_id,path,data) VALUES(?,?,?)',(wid,'main.tex',source.encode()))
            db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('latex.workspace.project.'+str(site_id),str(wid)))
        db.commit()
        return jsonify(ok=True,workspace_id=wid,editor_url=url_for('latex_workbench.editor',pid=wid,embed=1))

    def state_data(pid):
        p = project(pid);files = file_map(pid)
        entries = [{'path':n,'size':len(v),'text':v.decode('utf-8-sig') if PurePosixPath(n).suffix.lower() in TEXT_EXT else None} for n,v in files.items()]
        builds = [dict(r) for r in get_db().execute('SELECT id,version,status,created_at,finished_at FROM latex_builds WHERE project_id=? ORDER BY created_at DESC LIMIT 8',(pid,))]
        return dict(ok=True, project=p, files=entries, builds=builds,site_project_id=workspace_site_id(get_db(),pid))

    @bp.get('/<int:pid>')
    def editor(pid):
        initial=state_data(pid)
        return render_template('admin_latex_editor.html', project=initial['project'],embedded=request.args.get('embed')=='1',site_project_id=initial['site_project_id'],initial_state=initial)

    @bp.get('/<int:pid>/state')
    def state(pid):
        return jsonify(state_data(pid))

    @bp.post('/<int:pid>/save')
    def save(pid):
        body = json_body()
        db,p = checked_write(pid, body.get('version'))
        files = file_map(pid)
        title = body.get('title',p['title']);main = body.get('main_file',p['main_file']);engine = body.get('engine',p['engine'])
        if not isinstance(title,str) or not title.strip() or len(title)>120: raise WorkbenchError('项目名称需要 1–120 个字符。')
        if engine not in {'xelatex','pdflatex'}: raise WorkbenchError('请选择 XeLaTeX 或 pdfLaTeX。')
        changes = body.get('files',{});deleted = body.get('deleted',[])
        if not isinstance(changes,dict) or not isinstance(deleted,list) or len(changes)+len(deleted)>MAX_FILES: raise WorkbenchError('文件更新无效。')
        original = files.copy()
        for name in deleted:
            valid_path(name);files.pop(name,None)
        for name,text in changes.items():
            valid_path(name)
            if PurePosixPath(name).suffix.lower() not in TEXT_EXT or not isinstance(text,str):raise WorkbenchError('这里只能保存文本源码。')
            files[name] = text.encode('utf-8')
        validate_files(files)
        valid_path(main)
        if main not in files or not main.lower().endswith('.tex'):raise WorkbenchError('请选择存在的 .tex 文件作为主文件。')
        if files==original and (title.strip(),main,engine)==(p['title'],p['main_file'],p['engine']):
            db.rollback();return jsonify(ok=True,version=p['version'])
        for name in set(original)|set(files):
            if original.get(name)==files.get(name):continue
            if name in original:remember(db,pid,name,original[name])
            if name in files:db.execute('INSERT INTO latex_files(project_id,path,data) VALUES(?,?,?) ON CONFLICT(project_id,path) DO UPDATE SET data=excluded.data',(pid,name,files[name]))
            else:db.execute('DELETE FROM latex_files WHERE project_id=? AND path=?',(pid,name))
        return updated(db,pid,p,title.strip(),main,engine)

    @bp.post('/<int:pid>/upload')
    def upload(pid):
        try:version = int(request.form.get('version',''))
        except ValueError:raise WorkbenchError('项目版本无效。')
        additions = {}
        for f in request.files.getlist('files'):
            raw = f.stream.read(MAX_PROJECT+1)
            if len(raw)>MAX_PROJECT:raise WorkbenchError('上传文件过大。')
            name = f.filename or ''
            if name.lower().endswith('.zip'):
                try:
                    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                        members = [m for m in archive.infolist() if not m.is_dir()]
                        if len(members)>MAX_FILES or sum(m.file_size for m in members)>MAX_PROJECT:raise WorkbenchError('ZIP 解压后的文件数量或大小超限。')
                        for item in members:
                            if (item.external_attr>>16)&0o170000==0o120000:raise WorkbenchError('ZIP 不能包含符号链接。')
                            valid_path(item.filename)
                            if item.file_size>MAX_FILE:raise WorkbenchError('ZIP 内有超过 4 MB 的文件。')
                            additions[item.filename] = archive.read(item)
                except (zipfile.BadZipFile,RuntimeError,NotImplementedError):raise WorkbenchError('无法读取这个 ZIP 文件。')
            else:
                valid_path(name);additions[name]=raw
        if not additions:raise WorkbenchError('请选择上传文件。')
        db,p = checked_write(pid,version);existing=file_map(pid);files={**existing,**additions};validate_files(files)
        # Require explicit confirmation in the client before replacing existing files.
        overlap = sorted(set(existing)&set(additions))
        if overlap and request.form.get('replace')!='1':raise WorkbenchError('文件已存在：'+', '.join(overlap[:10])+'。确认覆盖后再上传。',409)
        for name,raw in additions.items():
            if name in existing and existing[name]!=raw:remember(db,pid,name,existing[name])
            db.execute('INSERT INTO latex_files(project_id,path,data) VALUES(?,?,?) ON CONFLICT(project_id,path) DO UPDATE SET data=excluded.data',(pid,name,raw))
        return updated(db,pid,p)

    @bp.get('/<int:pid>/file')
    def file(pid):
        project(pid);name=valid_path(request.args.get('path',''))
        row=get_db().execute('SELECT data FROM latex_files WHERE project_id=? AND path=?',(pid,name)).fetchone()
        if row is None:abort(404)
        preview=request.args.get('preview')=='1' and PurePosixPath(name).suffix.lower() in {'.png','.jpg','.jpeg'}
        return send_file(io.BytesIO(bytes(row['data'])),download_name=PurePosixPath(name).name,as_attachment=not preview)

    @bp.get('/<int:pid>/source.zip')
    def source_zip(pid):
        project(pid);output=io.BytesIO()
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
            for name,raw in file_map(pid).items():z.writestr(name,raw)
        output.seek(0)
        return send_file(output,mimetype='application/zip',as_attachment=True,download_name='latex-project-'+str(pid)+'.zip')

    @bp.get('/<int:pid>/history')
    def history(pid):
        project(pid);name=valid_path(request.args.get('path',''))
        rows=get_db().execute('SELECT id,path,length(content) AS size,created_at FROM latex_revisions WHERE project_id=? AND path=? ORDER BY id DESC LIMIT 30',(pid,name)).fetchall()
        return jsonify(ok=True,revisions=[dict(r) for r in rows])

    @bp.get('/<int:pid>/revision/<int:rid>')
    def revision(pid,rid):
        project(pid)
        row=get_db().execute('SELECT path,content FROM latex_revisions WHERE id=? AND project_id=?',(rid,pid)).fetchone()
        if row is None:abort(404)
        return jsonify(ok=True,path=row['path'],content=row['content'])

    def build_row(pid,bid):
        if not re.fullmatch('[a-f0-9]{32}',bid):abort(404)
        row=get_db().execute('SELECT * FROM latex_builds WHERE id=? AND project_id=?',(bid,pid)).fetchone()
        if row is None:abort(404)
        return dict(row)

    def finish_build(bid,pid,snapshot,engine,main,lock,previous=None):
        work=storage/'builds'/bid
        success=False;log='';error_list=[]
        try:
            work.mkdir(parents=True,mode=0o700)
            # Each build owns its cache. Never mount another project or a shared writable runtime.
            identity={'engine':engine,'main':main,'paths':sorted(snapshot)}
            if previous and previous.is_dir():
                cache=previous/'.tex-cache'
                if cache.is_dir():shutil.copytree(cache,work/'.tex-cache',symlinks=False)
                marker=previous/'.compile-input.json'
                if marker.is_file() and json.loads(marker.read_text())==identity:
                    for name in ['output.aux','output.toc','output.lof','output.lot','output.out','output.bbl']:
                        generated=previous/name
                        if generated.is_file() and generated.stat().st_size<=MAX_FILE:shutil.copyfile(generated,work/name)
            (work/'.compile-input.json').write_text(json.dumps(identity))
            for name,raw in snapshot.items():
                destination=work/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(raw)
            success,log=compile_snapshot(work,engine,main)
            if not success:error_list=errors_from_log(log,main)
            if not success and not error_list:error_list=[{'file':main,'line':1,'message':'编译失败。请展开日志查看详情。'}]
        except Exception as exc:
            log=str(exc) if isinstance(exc,WorkbenchError) else '编译任务失败，请稍后重试。'
            error_list=[{'file':main,'line':1,'message':log}]
        finally:
            try:
                with sqlite3.connect(db_path,timeout=15) as db:
                    db.execute("UPDATE latex_builds SET status=?,log=?,errors=?,finished_at=? WHERE id=?",('success' if success else 'error',log[-180000:],json.dumps(error_list,ensure_ascii=False),now_iso(),bid))
                    old=db.execute('SELECT id FROM latex_builds WHERE project_id=? ORDER BY created_at DESC LIMIT -1 OFFSET 8',(pid,)).fetchall()
                    for (old_id,) in old:
                        if re.fullmatch('[a-f0-9]{32}',old_id):shutil.rmtree(storage/'builds'/old_id,ignore_errors=True)
                        db.execute('DELETE FROM latex_builds WHERE id=?',(old_id,))
            finally:
                lock.close()

    @bp.post('/<int:pid>/compile')
    def compile_project(pid):
        p=project(pid);body=json_body()
        if body.get('version')!=p['version']:raise WorkbenchError('请先保存当前内容，再编译。',409)
        existing=get_db().execute("SELECT id FROM latex_builds WHERE project_id=? AND version=? AND status='success' ORDER BY created_at DESC LIMIT 1",(pid,p['version'])).fetchone()
        if existing and re.fullmatch('[a-f0-9]{32}',existing['id']) and (storage/'builds'/existing['id']/'output.pdf').is_file():
            return jsonify(ok=True,build_id=existing['id'],cached=True),202
        if os.name!='posix':raise WorkbenchError('本地开发环境不运行服务器 PDF 编译器。',503)
        # Check compiler readiness before returning a queued job.
        sandbox_command(Path('/tmp'),p['engine'],p['main_file'])
        import fcntl
        lock=(storage/'compile.lock').open('a+b')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close();raise WorkbenchError('已有一个编译任务正在运行，请稍后再试。',429)
        bid=secrets.token_hex(16);db=get_db()
        db.execute('BEGIN IMMEDIATE')
        p=project(pid)
        if body.get('version')!=p['version']:
            db.rollback();lock.close();raise WorkbenchError('源码刚刚发生变化，请重新编译。',409)
        snapshot=file_map(pid)
        prior=db.execute("SELECT id FROM latex_builds WHERE project_id=? AND status='success' ORDER BY created_at DESC LIMIT 1",(pid,)).fetchone()
        previous=storage/'builds'/prior['id'] if prior and re.fullmatch('[a-f0-9]{32}',prior['id']) else None
        db.execute("UPDATE latex_builds SET status='error',log='服务重启或任务中断，请重新编译。',finished_at=? WHERE status='running'",(now_iso(),))
        db.execute('INSERT INTO latex_builds(id,project_id,version,status,created_at) VALUES(?,?,?,?,?)',(bid,pid,p['version'],'running',now_iso()));db.commit()
        thread=threading.Thread(target=finish_build,args=(bid,pid,snapshot,p['engine'],p['main_file'],lock,previous),daemon=True)
        try:thread.start()
        except Exception:
            lock.close();db.execute("UPDATE latex_builds SET status='error',log='无法启动编译任务。',finished_at=? WHERE id=?",(now_iso(),bid));db.commit();raise
        return jsonify(ok=True,build_id=bid),202

    @bp.get('/<int:pid>/build/<bid>')
    def build_status(pid,bid):
        project(pid);row=build_row(pid,bid)
        if row['status']=='running' and (datetime.now(timezone.utc)-datetime.fromisoformat(row['created_at'])).total_seconds()>180:
            get_db().execute("UPDATE latex_builds SET status='error',log='编译任务已中断，请重新编译。',finished_at=? WHERE id=? AND status='running'",(now_iso(),bid));get_db().commit();row=build_row(pid,bid)
        row['errors']=json.loads(row['errors'])
        return jsonify(ok=True,build=row)

    @bp.get('/<int:pid>/build/<bid>/pdf')
    def pdf(pid,bid):
        project(pid);row=build_row(pid,bid)
        if row['status']!='success':abort(404)
        p=storage/'builds'/bid/'output.pdf'
        if not p.is_file():abort(404)
        return send_file(p,mimetype='application/pdf',as_attachment=request.args.get('download')=='1',download_name='latex-project-'+str(pid)+'.pdf')

    @bp.get('/<int:pid>/publish-targets')
    def publish_targets(pid):
        project(pid);db=get_db()
        return jsonify(ok=True,posts=[dict(r) for r in db.execute('SELECT id,title FROM posts ORDER BY updated_at DESC LIMIT 200')],projects=[dict(r) for r in db.execute('SELECT id,title FROM projects ORDER BY updated_at DESC LIMIT 200')])

    @bp.post('/<int:pid>/publish')
    def publish(pid):
        p=project(pid);body=json_body();build=build_row(pid,body.get('build_id',''))
        if body.get('version')!=p['version'] or build['version']!=p['version']:raise WorkbenchError('PDF 对应的源码版本已变化，请重新编译后发布。',409)
        if build['status']!='success':raise WorkbenchError('请先成功编译 PDF。')
        target=body.get('target','media');target_id=body.get('target_id')
        if target not in {'media','post','project'}:raise WorkbenchError('发布目标无效。')
        db=get_db();row=None
        if target!='media':
            if not isinstance(target_id,int):raise WorkbenchError('请选择文章或项目。')
            table='posts' if target=='post' else 'projects'
            row=db.execute('SELECT * FROM '+table+' WHERE id=?',(target_id,)).fetchone()
            if row is None:abort(404)
        linked = row is not None and target == 'project' and workspace_site_id(db, pid) == target_id
        article_source = None
        article_assets = {}
        if linked:
            try:
                article_source = workspace_article_source(file_map(pid), p['main_file'], assets=article_assets,
                    asset_url=lambda name: url_for('uploaded_file', filename=name))
            except LatexRenderError as exc:
                raise WorkbenchError('网页正文转换失败，原发布内容保持不变。' + str(exc)) from exc
        # The saved source must still match the compiled version after conversion.
        db, p = checked_write(pid, body.get('version'))
        raw=(storage/'builds'/build['id']/'output.pdf').read_bytes()
        filename='latex-'+str(pid)+'-'+secrets.token_hex(12)+'.pdf'
        destination=Path(upload_dir)/filename;destination.parent.mkdir(parents=True,exist_ok=True)
        public_url=url_for('uploaded_file',filename=filename)
        destination.write_bytes(raw)
        created_images = []
        try:
            for image_name, (original, mime, data) in article_assets.items():
                image_path = Path(upload_dir) / image_name
                try:
                    with image_path.open('xb') as image:
                        created_images.append(image_path)
                        image.write(data)
                except FileExistsError:
                    if image_path.read_bytes() != data:
                        raise WorkbenchError('已发布图片校验失败，请联系管理员。')
                if not db.execute('SELECT 1 FROM media WHERE filename=?', (image_name,)).fetchone():
                    db.execute('INSERT INTO media(original_name,filename,mime_type,size,created_at) VALUES(?,?,?,?,?)',
                        (original, image_name, mime, len(data), now_iso()))
            db.execute('INSERT INTO media(original_name,filename,mime_type,size,created_at) VALUES(?,?,?,?,?)',(p['title']+'.pdf',filename,'application/pdf',len(raw),now_iso()))
            if linked:
                db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('latex.pdf.project.'+str(target_id),public_url))
                db.execute('UPDATE projects SET content=?,content_format=?,updated_at=? WHERE id=?',(article_source,'latex',now_iso(),target_id))
            elif row is not None:
                suffix=('\n\n\\href{'+public_url+'}{在线阅读 / 下载 PDF}\n') if row['content_format']=='latex' else ('\n\n[在线阅读 / 下载 PDF]('+public_url+')\n')
                if target=='post':
                    db.execute('INSERT INTO post_revisions(post_id,title,excerpt,content,created_at,content_format) VALUES(?,?,?,?,?,?)',(row['id'],row['title'],row['excerpt'],row['content'],now_iso(),row['content_format']))
                db.execute('UPDATE '+table+' SET content=?,updated_at=? WHERE id=?',(row['content']+suffix,now_iso(),target_id))
            db.commit()
        except Exception:
            db.rollback();destination.unlink(missing_ok=True)
            for image_path in created_images: image_path.unlink(missing_ok=True)
            raise
        return jsonify(ok=True,url=public_url,message=('项目网页正文已更新，PDF 已保存。' if linked else 'PDF 已发布。'+('链接已添加到所选内容末尾。' if row is not None else '可复制链接添加到文章或项目。')))

    @bp.post('/<int:pid>/delete')
    def delete(pid):
        body=json_body();db,p=checked_write(pid,body.get('version'))
        if workspace_site_id(db,pid) is not None:raise WorkbenchError('这是项目内的文档，请从项目管理页管理项目；源码和编译记录会保留。',409)
        if db.execute("SELECT 1 FROM latex_builds WHERE project_id=? AND status='running'",(pid,)).fetchone():raise WorkbenchError('请等待当前编译结束后再删除项目。',409)
        # Keep an explicit recovery archive before deleting a workspace.
        backup=storage/'deleted';backup.mkdir(parents=True,exist_ok=True,mode=0o700)
        archive=backup/(str(pid)+'-'+secrets.token_hex(8)+'.zip')
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for n,raw in file_map(pid).items():z.writestr(n,raw)
            z.writestr('_project_metadata.json',json.dumps(p,ensure_ascii=False))

        builds=[r['id'] for r in db.execute('SELECT id FROM latex_builds WHERE project_id=?',(pid,))]
        db.execute('DELETE FROM latex_projects WHERE id=?',(pid,));db.commit()
        for bid in builds:
            if re.fullmatch('[a-f0-9]{32}',bid):shutil.rmtree(storage/'builds'/bid,ignore_errors=True)
        return jsonify(ok=True,url=url_for('latex_workbench.index'))

    app.register_blueprint(bp)
