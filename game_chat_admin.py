"""Admin-session bridge to the loopback-only game moderation API. No shared DB writes."""
import os
import hmac
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from flask import request, session, render_template, jsonify, Response

def install_game_chat_admin(app, admin_required, csrf_token):
    @app.get('/admin/game-chat')
    @admin_required
    def admin_game_chat():
        return render_template('admin_game_chat.html', section='chat')

    @app.get('/admin/game-chat/sensitive-words')
    @admin_required
    def admin_game_chat_rules():
        return render_template('admin_game_chat.html', section='rules')

    @app.route('/admin/game-chat/api/<path:route>', methods=['GET','POST','DELETE'])
    def admin_game_chat_api(route):
        if not session.get('admin_authenticated'):
            return jsonify(error='请先登录管理后台'), 401
        if not re.fullmatch(r'(?:session|overview|messages(?:/[a-zA-Z0-9_-]{1,120})?|events|penalties|rules(?:/[a-zA-Z0-9_-]{1,120})?|import|export|whitelist(?:/[a-zA-Z0-9_-]{1,120})?|records(?:/[a-zA-Z0-9_-]{1,120}/resolve)?|mutes|test|reload)', route):
            return jsonify(error='管理接口不存在'), 404
        if request.method != 'GET':
            expected=session.get('csrf_token','')
            received=request.headers.get('X-CSRF-Token','')
            origin=os.environ.get('ORANGE_PUBLIC_ORIGIN',request.host_url.rstrip('/'))
            if not expected or not hmac.compare_digest(expected.encode(),received.encode()) or request.headers.get('Origin') != origin:
                return jsonify(error='安全校验失败，请刷新后台重试'), 403
        if request.content_length and request.content_length > 12*1024*1024:
            return jsonify(error='导入文件不能超过 12MB'), 413
        try:
            key=os.environ.get('MODERATION_BRIDGE_SECRET') or Path(os.environ.get('MODERATION_BRIDGE_SECRET_FILE','/etc/pixel-ybu-moderation-bridge.key')).read_text().strip()
            if len(key)<32: raise ValueError('Missing bridge configuration')
            endpoint=app.config.get('GAME_MODERATION_INTERNAL','http://127.0.0.1:3001')+'/internal/moderation/'+route
            if request.query_string: endpoint+='?'+request.query_string.decode('ascii')
            # Actor comes from the authenticated server configuration, never from browser headers/body.
            actor=str(app.config.get('ADMIN_USER', '')) or os.environ.get('ORANGE_ADMIN_USER','orange')
            req=Request(endpoint,method=request.method,data=request.get_data() if request.method!='GET' else None,headers={'X-Moderation-Key':key,'X-Moderation-Actor':actor.encode('utf-8').hex(),'Content-Type':'application/json'})
            try:
                upstream=urlopen(req,timeout=15)
            except HTTPError as error:
                upstream=error
            with upstream:
                body=upstream.read(16*1024*1024)
                status=upstream.code
                if route=='session' and status==200:
                    data=json.loads(body);data['csrf']=csrf_token();body=json.dumps(data,ensure_ascii=False).encode()
                response=Response(body,status=status,content_type='application/json; charset=utf-8')
                response.headers['Cache-Control']='private, no-store'
                if route=='export' and status==200:response.headers['Content-Disposition']='attachment; filename="pixel-ybu-moderation.json"'
                return response
        except (OSError,ValueError,URLError):
            return jsonify(error='游戏审核服务暂不可用，请稍后重试'), 503
