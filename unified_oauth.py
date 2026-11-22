"""First-party OAuth 2.0 code grant, implemented by Authlib with required PKCE."""
import time, hmac, json, re, math
from types import SimpleNamespace
from flask import request, session, redirect
from authlib.integrations.flask_oauth2 import AuthorizationServer
from authlib.oauth2.rfc6749 import grants, ClientMixin
from authlib.oauth2.rfc7636 import CodeChallenge
from authlib.integrations.flask_oauth2.requests import FlaskOAuth2Payload, FlaskOAuth2Request
from authlib.oauth2.rfc6749 import OAuth2Request
from unified_sms import digest, AccountError
from unified_schema import complete

class Client(ClientMixin):
    client_id='pixel-ybu'
    def __init__(self,app): self.app=app
    def get_client_id(self): return self.client_id
    def get_default_redirect_uri(self): return self.app.config['UNIFIED_GAME_CALLBACK']
    def check_redirect_uri(self,uri): return uri==self.get_default_redirect_uri()
    def get_allowed_scope(self,scope): return ' '.join(s for s in (scope or 'game').split() if s=='game')
    def check_client_secret(self,secret): return bool(secret) and hmac.compare_digest(secret,self.app.config['UNIFIED_GAME_SECRET'])
    def check_endpoint_auth_method(self,method,endpoint): return method=='client_secret_basic'
    def check_response_type(self,value): return value=='code'
    def check_grant_type(self,value): return value=='authorization_code'

class User(SimpleNamespace):
    def get_user_id(self): return self.id

def install_oauth(app,get_db,current_user,upgrade_redirect):
    class CanonicalRequest(FlaskOAuth2Request):
        def __init__(self,req):
            # Nginx/Gunicorn and the authenticated loopback game client use HTTP
            # internally. The externally configured issuer owns protocol URLs;
            # untrusted X-Forwarded-* values never change the OAuth authority.
            uri=app.config['UNIFIED_PUBLIC_ORIGIN']+req.full_path.rstrip('?')
            OAuth2Request.__init__(self,method=req.method,uri=uri,headers=req.headers)
            self._request=req;self.payload=FlaskOAuth2Payload(req)
    class CanonicalServer(AuthorizationServer):
        def create_oauth2_request(self,unused): return CanonicalRequest(request)
    client=Client(app)
    class Code(grants.AuthorizationCodeGrant):
        TOKEN_ENDPOINT_AUTH_METHODS=['client_secret_basic']
        def save_authorization_code(self,code,req):
            now=int(time.time()); db=get_db()
            payload=req.payload
            with db:
                db.execute('DELETE FROM unified_codes WHERE expires_at<?',(now-86400,))
                db.execute('INSERT INTO unified_codes VALUES(?,?,?,?,?,?,?,?,NULL)',
                    (digest(code),req.user.id,req.user.version,payload.redirect_uri,payload.scope or 'game',
                     payload.data.get('code_challenge'),payload.data.get('code_challenge_method'),now+60))
        def query_authorization_code(self,code,client):
            # Atomic claim before validating PKCE: even concurrent exchanges cannot both succeed.
            db=get_db();now=int(time.time())
            with db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT * FROM unified_codes WHERE code_hash=? AND consumed_at IS NULL AND expires_at>?',(digest(code),now)).fetchone()
                if not row: return None
                db.execute('UPDATE unified_codes SET consumed_at=? WHERE code_hash=?',(now,digest(code)))
            class Saved(SimpleNamespace):
                def get_redirect_uri(self): return self.redirect_uri
                def get_scope(self): return self.scope
            return Saved(**dict(row))
        def delete_authorization_code(self,code): pass
        def authenticate_user(self,code):
            row=get_db().execute('SELECT * FROM reader_users WHERE id=?',(code.user_id,)).fetchone()
            if complete(row) and row['session_version']==code.session_version:
                return User(id=row['id'],version=row['session_version'])
    def save_token(token,req):
        db=get_db();now=int(time.time())
        with db:
            db.execute('DELETE FROM unified_tokens WHERE expires_at<?',(now-86400,))
            db.execute('INSERT INTO unified_tokens VALUES(?,?,?,?,?,NULL)',
                (digest(token['access_token']),req.user.id,req.user.version,'game',now+int(token['expires_in'])))
    app.config['OAUTH2_TOKEN_EXPIRES_IN']={'authorization_code':43200}
    server=CanonicalServer(app,query_client=lambda cid:client if cid=='pixel-ybu' else None,save_token=save_token)
    class RequiredPKCE(CodeChallenge):
        SUPPORTED_CODE_CHALLENGE_METHOD=['S256']
    server.register_grant(Code,[RequiredPKCE(required=True)])

    @app.get('/oauth/authorize')
    def unified_authorize():
        # Validate fixed first-party requests before creating a login continuation.
        args=request.args
        if args.get('client_id')!='pixel-ybu' or args.get('redirect_uri')!=app.config['UNIFIED_GAME_CALLBACK'] or args.get('response_type')!='code' or args.get('scope','game')!='game' or args.get('code_challenge_method')!='S256' or not re.fullmatch('[A-Za-z0-9_-]{43}',args.get('code_challenge','')) or not 32<=len(args.get('state',''))<=128:
            return {'error':'invalid_request'},400
        user=current_user()
        if not user:
            session['unified_authorize_next']=request.full_path.rstrip('?')
            return redirect('/auth/login?next=/auth/continue')
        row=get_db().execute('SELECT * FROM reader_users WHERE id=?',(user['id'],)).fetchone()
        if not complete(row):
            session['unified_authorize_next']=request.full_path.rstrip('?')
            return upgrade_redirect('/auth/continue')
        return server.create_authorization_response(grant_user=User(id=row['id'],version=row['session_version']))

    @app.post('/oauth/token')
    def unified_token(): return server.create_token_response()

    def principal():
        # Only the game backend knows client credentials, even if a browser steals a bearer token.
        auth=request.authorization
        if not auth or auth.type.lower()!='basic' or auth.username!='pixel-ybu' or not client.check_client_secret(auth.password or ''):
            raise AccountError('无效的服务凭据。',401)
        token=request.headers.get('X-Orange-Token','')
        if not 20<=len(token)<=256: raise AccountError('登录已失效。',401)
        row=get_db().execute('SELECT u.*,t.expires_at AS token_expires,t.session_version AS token_version FROM unified_tokens t JOIN reader_users u ON u.id=t.user_id WHERE t.token_hash=? AND t.revoked_at IS NULL AND t.expires_at>?',(digest(token),int(time.time()))).fetchone()
        if not complete(row) or row['session_version']!=row['token_version']: raise AccountError('登录已失效或账号需要升级。',401)
        return row

    def game_data(user):
        row=get_db().execute('SELECT * FROM game_profiles WHERE user_id=?',(user['id'],)).fetchone()
        follows=[r[0] for r in get_db().execute('SELECT target_id FROM game_follows WHERE user_id=?',(user['id'],))]
        followers=get_db().execute('SELECT count(*) FROM game_follows WHERE target_id=?',(user['id'],)).fetchone()[0]
        return {'ok':True,'user':{'id':user['id'],'username':user['nickname']},'expires_at':user['token_expires'],'followers':followers,
                'state':json.loads(row['state_json']) if row else None,'revision':row['revision'] if row else 0,'follows':follows}

    @app.get('/internal/game/session')
    def unified_game_session(): return game_data(principal())

    @app.route('/internal/game/state',methods=['GET','PUT'])
    def unified_game_state():
        user=principal()
        if request.method=='GET': return game_data(user)
        body=request.get_json(silent=True) or {}
        if not isinstance(body,dict): raise AccountError('请求格式无效。',400)
        data=body.get('state',{})
        if not isinstance(data,dict) or len(json.dumps(data))>8192: raise AccountError('存档格式无效。',400)
        # Coordinates are authoritative at the game backend; this endpoint is inaccessible to frontend claims.
        allowed={'scene','x','y','avatar','worldVersion','visited'}
        if set(data)-allowed: raise AccountError('存档包含未知字段。',400)
        if any(k in data and (type(data[k]) not in (int,float) or not math.isfinite(data[k]) or not 0<=data[k]<=1000000) for k in ('x','y')):
            raise AccountError('存档坐标无效。',400)
        if 'avatar' in data and (type(data['avatar']) is not int or not 0<=data['avatar']<14): raise AccountError('存档角色无效。',400)
        if 'visited' in data and (not isinstance(data['visited'],list) or len(data['visited'])>200 or any(not isinstance(v,str) or len(v)>64 for v in data['visited'])): raise AccountError('探索记录无效。',400)
        if type(body.get('revision')) is not int: raise AccountError('缺少存档版本。',400)
        db=get_db();now=int(time.time())
        with db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT revision FROM game_profiles WHERE user_id=?',(user['id'],)).fetchone()
            revision=row['revision'] if row else 0
            if revision!=body['revision']: raise AccountError('存档已被另一设备更新，请重新进入游戏。',409)
            db.execute('INSERT INTO game_profiles VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET state_json=excluded.state_json,revision=excluded.revision,updated_at=excluded.updated_at',
                       (user['id'],json.dumps(data,ensure_ascii=False),revision+1,now))
        return {'ok':True,'revision':revision+1}

    @app.put('/internal/game/follow')
    def unified_game_follow():
        user=principal(); data=request.get_json(silent=True) or {}
        if not isinstance(data,dict): raise AccountError('请求格式无效。',400)
        target=data.get('target_id')
        if type(target) is not int or target==user['id'] or type(data.get('following')) is not bool: raise AccountError('无效关注。',400)
        db=get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM reader_users WHERE id=? AND disabled_at IS NULL',(target,)).fetchone(): raise AccountError('账号不存在。',404)
            if data['following']:
                if db.execute('SELECT count(*) FROM game_follows WHERE user_id=?',(user['id'],)).fetchone()[0]>=100: raise AccountError('最多关注100人。')
                db.execute('INSERT OR IGNORE INTO game_follows VALUES(?,?,?)',(user['id'],target,int(time.time())))
            else: db.execute('DELETE FROM game_follows WHERE user_id=? AND target_id=?',(user['id'],target))
        return {'ok':True}

    @app.post('/internal/game/logout')
    def unified_game_logout():
        user=principal();db=get_db()
        with db:
            db.execute('UPDATE reader_users SET session_version=session_version+1 WHERE id=?',(user['id'],))
            db.execute('UPDATE unified_tokens SET revoked_at=? WHERE user_id=?',(int(time.time()),user['id']))
        return {'ok':True}
    return server
