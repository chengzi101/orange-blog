"""Session/purpose-bound SMS challenges; network calls never hold a SQLite write lock."""
import os, re, secrets, time, hashlib, hmac, json
from flask import session, g
from moderation import client_ip

TEMPLATES={'register':'100001','login':'100001','change':'100002','recover':'100003','bind':'100004','old-phone':'100005'}

class AccountError(Exception):
    def __init__(self, message, status=422): self.message,self.status=message,status

def phone_number(value):
    if not isinstance(value,str) or not re.fullmatch(r'1[3-9][0-9]{9}',value):
        raise AccountError('请输入正确的中国大陆11位手机号。')
    return value

def digest(value): return hashlib.sha256(value.encode()).hexdigest()

class Sms:
    def __init__(self, app, get_db): self.app,self.db=app,get_db

    def binding(self):
        if getattr(g, 'unified_game_sms_binding', None):
            return digest(g.unified_game_sms_binding)
        if not session.get('unified_sms_key'): session['unified_sms_key']=secrets.token_urlsafe(32)
        return digest(session['unified_sms_key'])

    def reserve(self, kind, dimensions):
        from public_limits import reserve_public
        from auth_security import LoginLimited
        try: reserve_public(self.app,self.db(),kind,dimensions)
        except LoginLimited: raise AccountError('操作太频繁，请稍后再试。',429)

    def provider(self):
        mode=self.app.config['UNIFIED_SMS_MODE']
        if mode=='mock': return None
        if mode!='aliyun': raise AccountError('短信服务未配置。',503)
        try:
            from alibabacloud_dypnsapi20170525.client import Client
            from alibabacloud_tea_openapi.models import Config
            config=Config(access_key_id=os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'],
                          access_key_secret=os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'],
                          connect_timeout=3000,read_timeout=5000)
            config.endpoint='dypnsapi.aliyuncs.com'
            return Client(config)
        except (KeyError,ImportError): raise AccountError('短信服务暂不可用，请稍后重试。',503)

    def send(self, phone, purpose, user_id=None):
        phone=phone_number(phone)
        if not isinstance(purpose,str) or purpose not in TEMPLATES: raise AccountError('不支持的验证码用途。',400)
        self.reserve('unified-sms-send',[
            ('phone-minute',phone,1,60),('phone-hour',phone,5,3600),('phone-day',phone,10,86400),
            ('ip-hour',client_ip(),15,3600),('ip-day',client_ip(),40,86400),
            ('global-hour','all',self.app.config['UNIFIED_SMS_HOUR_LIMIT'],3600),
            ('global-day','all',self.app.config['UNIFIED_SMS_DAY_LIMIT'],86400)])
        provider=self.provider(); now=int(time.time()); cid=secrets.token_urlsafe(24)
        # Random mock codes are available only to an explicitly configured local outbox.
        code=''.join(secrets.choice('0123456789') for _ in range(6)) if provider is None else None
        encoded=hmac.new(str(self.app.secret_key).encode(),(cid+str(code)).encode(),hashlib.sha256).hexdigest() if code else None
        db=self.db()
        with db:
            db.execute('UPDATE unified_sms SET consumed_at=? WHERE session_key=? AND purpose=? AND consumed_at IS NULL', (now,self.binding(),purpose))
            db.execute('DELETE FROM unified_sms WHERE expires_at<?',(now-86400,))
            db.execute('INSERT INTO unified_sms(id,session_key,user_id,phone,purpose,provider,mock_hash,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)',
                       (cid,self.binding(),user_id,phone,purpose,self.app.config['UNIFIED_SMS_MODE'],encoded,now,now+300))
        try:
            if provider:
                from alibabacloud_dypnsapi20170525.models import SendSmsVerifyCodeRequest
                req=SendSmsVerifyCodeRequest(phone_number=phone,country_code='86',out_id=cid,
                    sign_name=self.app.config['UNIFIED_SMS_SIGN_NAME'],template_code=self.app.config['UNIFIED_SMS_TEMPLATES'][purpose],
                    template_param=json.dumps({'code':'##code##','min':'5'}),valid_time=300,code_length=6,
                    interval=60,return_verify_code=False)
                body=provider.send_sms_verify_code(req).body
                if body.code!='OK' or body.success is not True: raise AccountError('短信发送失败，请稍后再试。',503)
            elif self.app.config.get('UNIFIED_MOCK_OUTBOX') is not None:
                self.app.config['UNIFIED_MOCK_OUTBOX'][cid]=code
        except Exception:
            with db: db.execute('UPDATE unified_sms SET consumed_at=? WHERE id=?',(now,cid))
            raise AccountError('短信发送失败，请稍后再试。',503) from None
        with db: db.execute('UPDATE unified_sms SET ready=1 WHERE id=?',(cid,))
        return {'ok':True,'challenge_id':cid,'expires_in':300,'retry_after':60}

    def verify(self,cid,phone,purpose,code,user_id=None):
        phone_number(phone)
        if not isinstance(code,str) or not re.fullmatch(r'[0-9]{6}',code): raise AccountError('验证码需为6位数字。')
        self.reserve('unified-sms-verify',[('ip',client_ip(),60,900),('phone',phone,20,900),('global','all',500,900)])
        db=self.db();now=int(time.time())
        with db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM unified_sms WHERE id=?',(cid,)).fetchone()
            if not row or row['session_key']!=self.binding() or row['phone']!=phone or row['purpose']!=purpose or row['user_id']!=user_id or row['consumed_at'] or not row['ready'] or row['expires_at']<=now or row['attempts']>=5:
                raise AccountError('验证码已失效或尝试过多，请重新获取。')
            db.execute('UPDATE unified_sms SET attempts=attempts+1 WHERE id=?',(cid,))
        # An existing verified proof can be consumed atomically after an earlier DB conflict.
        if row['verified_at']:
            encoded=hmac.new(str(self.app.secret_key).encode(),(cid+code).encode(),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(encoded,row['mock_hash'] or ''): raise AccountError('验证码错误，请重试。')
            return cid
        if row['provider']=='mock':
            encoded=hmac.new(str(self.app.secret_key).encode(),(cid+code).encode(),hashlib.sha256).hexdigest()
            valid=hmac.compare_digest(encoded,row['mock_hash'] or '')
        else:
            try:
                from alibabacloud_dypnsapi20170525.models import CheckSmsVerifyCodeRequest
                provider=self.provider()
                body=provider.check_sms_verify_code(CheckSmsVerifyCodeRequest(phone_number=phone,country_code='86',out_id=cid,verify_code=code)).body
                valid=body.code=='OK' and body.success is True and body.model is not None and body.model.verify_result=='PASS'
            except Exception: raise AccountError('验证码服务暂不可用，请稍后重试。',503) from None
        if not valid: raise AccountError('验证码错误，请重试。')
        with db:
            encoded=hmac.new(str(self.app.secret_key).encode(),(cid+code).encode(),hashlib.sha256).hexdigest()
            changed=db.execute('UPDATE unified_sms SET verified_at=?,mock_hash=? WHERE id=? AND consumed_at IS NULL AND expires_at>?', (now,encoded,cid,now)).rowcount
            if changed!=1: raise AccountError('验证码已失效，请重新获取。')
        return cid

    def consume(self,db,cid,phone,purpose,user_id=None):
        # Caller owns BEGIN IMMEDIATE: proof and account mutation succeed together.
        now=int(time.time())
        changed=db.execute('UPDATE unified_sms SET consumed_at=? WHERE id=? AND session_key=? AND phone=? AND purpose=? AND user_id IS ? AND verified_at IS NOT NULL AND consumed_at IS NULL AND expires_at>?',
                           (now,cid,self.binding(),phone,purpose,user_id,now)).rowcount
        if changed!=1: raise AccountError('验证码已使用或过期，请重新获取。')
