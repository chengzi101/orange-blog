"""Unified reader account policy, registration, upgrade, recovery and phone change."""
import os, secrets, sqlite3, time
from urllib.parse import urlsplit
from flask import request, session, redirect, render_template, g, abort
from unified_schema import complete
from unified_sms import Sms, AccountError, TEMPLATES
from unified_passwords import validate_username, validate_password, generate_password_hash
from unified_oauth import install_oauth

UPGRADE_MESSAGE='为了保障账号安全，OrangeLab 现已升级统一账号系统。请绑定手机号，完成后即可继续使用 Orange 手记与像素延大。原账号和全部数据将完整保留。'

def safe_next(value):
    if not isinstance(value,str) or not value.startswith('/') or value.startswith('//') or '\\' in value or any(ord(c)<32 for c in value): return '/'
    p=urlsplit(value)
    if p.scheme or p.netloc or p.path.startswith(('/oauth/','/internal/','/admin','/auth/login','/auth/register','/auth/upgrade','/auth/recover','/auth/phone')): return '/'
    return value

def install_unified(app,get_db,now_iso):
    production=os.environ.get('ORANGE_ENV','production')=='production'
    app.config.update(
        UNIFIED_SMS_MODE=os.environ.get('ORANGE_SMS_MODE','mock'),
        UNIFIED_SMS_SIGN_NAME=os.environ.get('ALIYUN_SMS_SIGN_NAME','恒创联众'),
        UNIFIED_SMS_TEMPLATES={purpose:os.environ.get('ALIYUN_SMS_TEMPLATE_'+purpose.upper().replace('-','_'),code) for purpose,code in TEMPLATES.items()},
        UNIFIED_SMS_HOUR_LIMIT=int(os.environ.get('ORANGE_SMS_HOUR_LIMIT','50')),
        UNIFIED_SMS_DAY_LIMIT=int(os.environ.get('ORANGE_SMS_DAY_LIMIT','200')),
        UNIFIED_GAME_SECRET=os.environ.get('ORANGE_GAME_CLIENT_SECRET',''),
        UNIFIED_GAME_CALLBACK=os.environ.get('ORANGE_GAME_CALLBACK','https://game.orangelab.top/auth/callback'),
        UNIFIED_PUBLIC_ORIGIN=os.environ.get('ORANGE_PUBLIC_ORIGIN','https://www.orangelab.top'),
        UNIFIED_LEGACY_ORIGIN=os.environ.get('ORANGE_LEGACY_ORIGIN','https://orangelab.top'),
    )
    if production and app.config['UNIFIED_SMS_MODE']!='aliyun':
        raise RuntimeError('Production requires ORANGE_SMS_MODE=aliyun; mock is local-only')
    if len(app.config['UNIFIED_GAME_SECRET'])<32: raise RuntimeError('ORANGE_GAME_CLIENT_SECRET must be at least 32 characters')
    sms=Sms(app,get_db);app.extensions['unified_sms']=sms

    def user():
        uid=session.get('reader_id')
        if not isinstance(uid,int): return None
        row=get_db().execute('SELECT * FROM reader_users WHERE id=?',(uid,)).fetchone()
        if not row or row['disabled_at'] or row['account_status']!='active' or row['session_version']!=session.get('reader_version',0):
            for key in ('reader_id','reader_version','reader_csrf'): session.pop(key,None)
            return None
        return row

    def csrf():
        if not session.get('reader_csrf'): session['reader_csrf']=secrets.token_urlsafe(32)
        return session['reader_csrf']

    def validate_csrf():
        received=request.form.get('csrf_token','') or request.headers.get('X-CSRF-Token','')
        if not csrf() or not secrets.compare_digest(csrf(),received): raise AccountError('页面已过期，请刷新后重试。',400)
        origin=request.headers.get('Origin')
        # Production Gunicorn is behind a local TLS proxy. Compare against configured
        # public origin rather than trusting arbitrary forwarded headers.
        origins=(app.config['UNIFIED_PUBLIC_ORIGIN'],app.config['UNIFIED_LEGACY_ORIGIN'])
        expected=next((value for value in origins if urlsplit(value).netloc==request.host),app.config['UNIFIED_PUBLIC_ORIGIN'])
        if origin and origin!=expected: raise AccountError('请求来源无效。',403)

    def establish(row):
        # Reader authentication cannot promote an administrator.
        session['reader_id']=row['id'];session['reader_version']=row['session_version']
        session['reader_csrf']=secrets.token_urlsafe(32);session.pop('unified_sms_key',None)
        session.permanent=True;g.pop('reader_user',None)

    def upgrade_redirect(destination='/'):
        session['unified_return']=safe_next(destination)
        return redirect('/auth/upgrade',code=303)

    def page(mode,error='',status=200):
        row=user()
        return render_template('unified_auth.html',mode=mode,error=error,user=row,
            message=UPGRADE_MESSAGE,csrf=csrf(),next_url=safe_next(request.values.get('next','/')),
            mock=app.config['UNIFIED_SMS_MODE']=='mock'),status

    @app.errorhandler(AccountError)
    def failure(exc):
        get_db().rollback()
        if request.is_json or request.path.startswith(('/api/','/internal/')):
            return {'ok':False,'message':exc.message},exc.status
        if request.endpoint == 'unified_phone':
            row = user()
            if not row: return redirect('/auth/login?next=/account')
            return app.extensions['reader_profile_page'](row, code=exc.status, phone_error=exc.message)
        return page({'reader.register':'register','unified_upgrade':'upgrade','unified_legacy_upgrade':'upgrade','unified_phone':'phone','unified_recover':'recover'}.get(request.endpoint,'recover'),exc.message,exc.status)

    @app.errorhandler(sqlite3.OperationalError)
    def storage_unavailable(exc):
        app.logger.warning('Database operation unavailable in account request')
        response=app.make_response(({'ok':False,'message':'服务暂时繁忙，请稍后重试。'},503))
        response.headers['Retry-After']='5';response.headers['Cache-Control']='no-store'
        return response

    @app.errorhandler(ValueError)
    def invalid_input(exc):
        # Only account routes raise validation ValueError intentionally.
        if request.endpoint in ('reader.register','unified_upgrade','unified_legacy_upgrade','unified_phone','unified_recover'):
            return failure(AccountError(str(exc)))
        raise exc

    @app.before_request
    def require_account_upgrade():
        if request.path.startswith(('/static/','/reader/avatar/','/internal/','/oauth/token')): return
        if request.path.startswith('/admin') and session.get('admin_authenticated'): return
        row=user()
        if not row or complete(row): return
        allowed={'unified_upgrade','unified_legacy_upgrade','unified_sms_send','unified_recover','reader.logout','profile.account','reader.reader_session'}
        if request.endpoint in allowed: return
        if request.endpoint=='unified_continue': return upgrade_redirect('/auth/continue')
        if request.path.startswith('/oauth/authorize'):
            session['unified_authorize_next']=request.full_path.rstrip('?')
            return upgrade_redirect('/auth/continue')
        # Every authenticated business operation is intercepted, including historical sessions.
        if request.path.startswith('/api/'):
            return {'ok':False,'message':UPGRADE_MESSAGE,'upgrade_url':'/auth/upgrade'},403
        return upgrade_redirect(request.full_path.rstrip('?'))

    def register():
        if request.method=='GET': return page('register')
        validate_csrf()
        sms.reserve('unified-register',[('ip',__import__('moderation').client_ip(),10,3600),('global','all',100,86400)])
        nickname=validate_username(request.form.get('nickname',''));password=validate_password(request.form.get('password',''))
        phone=request.form.get('phone','');cid=request.form.get('challenge_id','')
        if password!=request.form.get('confirm_password',password): raise AccountError('两次输入的密码不一致。')
        sms.verify(cid,phone,'register',request.form.get('code',''))
        encoded=generate_password_hash(password);db=get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE');sms.consume(db,cid,phone,'register')
                uid=db.execute('INSERT INTO reader_users(nickname,nickname_key,password_hash,created_at,phone,phone_verified_at) VALUES(?,?,?,?,?,?)',
                    (nickname,nickname.casefold(),encoded,now_iso(),phone,int(time.time()))).lastrowid
        except sqlite3.IntegrityError: raise AccountError('用户名或手机号已被使用。如账号属于你，请使用账号恢复。',409)
        establish(db.execute('SELECT * FROM reader_users WHERE id=?',(uid,)).fetchone())
        return redirect(safe_next(request.form.get('next','/')),code=303)
    app.view_functions['reader.register']=register

    # Keep the existing scrypt login limits and credential rules; redirect immediately after success.
    previous_login=app.view_functions['reader.login']
    def login():
        response=app.make_response(previous_login())
        row=user()
        if request.method=='POST' and response.status_code in (302,303) and row and not complete(row):
            return upgrade_redirect(request.form.get('next','/'))
        return response
    app.view_functions['reader.login']=login

    previous_logout=app.view_functions['reader.logout']
    def logout():
        validate_csrf();row=user()
        if row:
            db=get_db()
            with db:
                db.execute('UPDATE reader_users SET session_version=session_version+1 WHERE id=?',(row['id'],))
                db.execute('UPDATE unified_tokens SET revoked_at=? WHERE user_id=?',(int(time.time()),row['id']))
        return previous_logout()
    app.view_functions['reader.logout']=logout

    @app.post('/api/auth/sms/send')
    def unified_sms_send():
        validate_csrf();data=request.get_json(silent=True) or request.form
        if not hasattr(data,'get'): raise AccountError('请求格式无效。',400)
        purpose=data.get('purpose','');phone=data.get('phone','');row=user()
        if purpose in ('bind','change','old-phone'):
            if not row: raise AccountError('请先登录。',401)
            if purpose=='bind' and complete(row): raise AccountError('请使用手机号换绑功能。',409)
            if purpose in ('change','old-phone'):
                if row['phone_change_used']: raise AccountError('已用完 1 次手机号更换机会。',409)
                if purpose=='change' and phone==row['phone']: raise AccountError('新手机号不能与当前手机号相同。')
                if not complete(row): raise AccountError('请先完成手机号绑定。',409)
            if purpose=='old-phone': phone=row['phone']
        # Return the same response for existing and new phones. Conflict is checked after verification.
        return sms.send(phone,purpose,row['id'] if purpose in ('bind','change','old-phone') else None)

    @app.route('/auth/upgrade',methods=['GET','POST'])
    def unified_upgrade():
        row=user()
        if not row: return redirect('/auth/login?next=/auth/continue')
        if request.method=='GET':
            if complete(row): return redirect(safe_next(session.pop('unified_return','/')))
            return page('upgrade')
        validate_csrf();uid=row['id'];phone=request.form.get('phone','');cid=request.form.get('challenge_id','')
        # Old username/hash are preserved exactly; missing fields alone require completion.
        nickname=validate_username(request.form.get('nickname','')) if not row['nickname'] or not row['nickname_key'] else row['nickname']
        encoded=generate_password_hash(validate_password(request.form.get('password',''))) if not row['password_hash'] else row['password_hash']
        sms.verify(cid,phone,'bind',request.form.get('code',''),uid)
        db=get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE');sms.consume(db,cid,phone,'bind',uid)
                changed=db.execute("UPDATE reader_users SET phone=?,phone_verified_at=?,nickname=?,nickname_key=?,password_hash=? WHERE id=? AND session_version=? AND (phone_verified_at IS NULL OR phone IS NULL OR nickname='' OR nickname_key='' OR password_hash='')",
                    (phone,int(time.time()),nickname,row['nickname_key'] or nickname.casefold(),encoded,uid,row['session_version'])).rowcount
                if changed!=1: raise AccountError('账号状态已变化，请重新登录。',409)
        except sqlite3.IntegrityError: raise AccountError('该手机号已绑定其他账号，无法覆盖。可退出后恢复该手机对应账号，或联系管理员核实归属。',409)
        return redirect(safe_next(session.pop('unified_return','/')),code=303)

    @app.post('/')
    def unified_legacy_upgrade():
        # Already-open legacy forms may submit to the homepage. Keep their body
        # and run the same authenticated CSRF/SMS checks as /auth/upgrade.
        required = ('csrf_token', 'phone', 'challenge_id', 'code')
        if not all(key in request.form for key in required): abort(405)
        return unified_upgrade()

    @app.get('/auth/continue')
    def unified_continue():
        path=session.pop('unified_authorize_next',None)
        if path and path.startswith('/oauth/authorize?'): return redirect(path)
        return redirect(safe_next(session.pop('unified_return','/')))

    @app.route('/auth/recover',methods=['GET','POST'])
    def unified_recover():
        if request.method=='GET': return page('recover')
        validate_csrf();phone=request.form.get('phone','');cid=request.form.get('challenge_id','')
        password=validate_password(request.form.get('password',''))
        sms.verify(cid,phone,'recover',request.form.get('code',''))
        encoded=generate_password_hash(password)
        db=get_db()
        with db:
            db.execute('BEGIN IMMEDIATE');sms.consume(db,cid,phone,'recover')
            row=db.execute('SELECT * FROM reader_users WHERE phone=? AND phone_verified_at IS NOT NULL AND disabled_at IS NULL AND account_status=\'active\'',(phone,)).fetchone()
            if not row: raise AccountError('无法恢复该账号。未绑定手机的历史账号请联系管理员核实归属。')
            db.execute('UPDATE reader_users SET password_hash=?,session_version=session_version+1 WHERE id=?',(encoded,row['id']))
            db.execute('UPDATE unified_tokens SET revoked_at=? WHERE user_id=?',(int(time.time()),row['id']))
        # A phone alone never gains administrator authentication, even for the owner reader.
        for key in ('reader_id','reader_version','reader_csrf'): session.pop(key,None)
        return redirect('/auth/login',code=303)

    @app.route('/auth/phone',methods=['GET','POST'])
    def unified_phone():
        row=user()
        if not row: return redirect('/auth/login?next=/auth/phone')
        if request.method=='GET': return redirect('/account#phone-security')
        validate_csrf()
        if row['phone_change_used']: raise AccountError('已用完 1 次手机号更换机会。',409)
        if not complete(row): raise AccountError('请先完成手机号绑定。',409)
        phone=request.form.get('phone','');cid=request.form.get('challenge_id','');old=request.form.get('old_challenge_id','')
        if phone==row['phone']: raise AccountError('新手机号不能与当前手机号相同。')
        sms.verify(old,row['phone'],'old-phone',request.form.get('old_code',''),row['id'])
        sms.verify(cid,phone,'change',request.form.get('code',''),row['id'])
        db=get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE');sms.consume(db,old,row['phone'],'old-phone',row['id']);sms.consume(db,cid,phone,'change',row['id'])
                changed=db.execute('UPDATE reader_users SET phone=?,phone_verified_at=?,phone_change_used=1,session_version=session_version+1 WHERE id=? AND phone=? AND session_version=? AND phone_change_used=0',
                    (phone,int(time.time()),row['id'],row['phone'],row['session_version'])).rowcount
                if changed!=1: raise AccountError('账号状态已变化，请重新登录。',409)
                db.execute('UPDATE unified_tokens SET revoked_at=? WHERE user_id=?',(int(time.time()),row['id']))
        except sqlite3.IntegrityError: raise AccountError('手机号已被其他账号使用，无法覆盖。',409)
        establish(db.execute('SELECT * FROM reader_users WHERE id=?',(row['id'],)).fetchone())
        return redirect('/account?phone=changed#phone-security',code=303)

    @app.after_request
    def unified_headers(response):
        if request.endpoint == 'unified_legacy_upgrade' or request.path.startswith(('/auth/','/oauth/','/internal/','/api/auth/')):
            response.headers['Cache-Control']='no-store'
            response.headers['Referrer-Policy']='same-origin' if request.path.startswith('/auth/') else 'no-referrer'
        return response

    install_oauth(app,get_db,user,upgrade_redirect)
    from unified_phone_login import install_phone_login
    install_phone_login(app,get_db,now_iso,sms)
