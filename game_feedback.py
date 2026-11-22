"""Admin bridge to the loopback-only game feedback API. The blog never touches the game database."""
import os
import hmac
import re
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from flask import request, session, render_template, jsonify, Response


def install_game_feedback_admin(app, admin_required, csrf_token):
    @app.get('/admin/game-feedback')
    @admin_required
    def admin_game_feedback():
        return render_template('admin_game_feedback.html', section='feedback')

    @app.route('/admin/game-feedback/api', methods=['GET'])
    @app.route('/admin/game-feedback/api/<path:route>', methods=['GET', 'POST', 'DELETE'])
    def admin_game_feedback_api(route=''):
        if not session.get('admin_authenticated'):
            return jsonify(error='请先登录管理后台'), 401
        if not re.fullmatch(r'(?:[a-zA-Z0-9-]{1,120}(?:/status)?)?', route or ''):
            return jsonify(error='管理接口不存在'), 404
        if request.method != 'GET':
            expected = session.get('csrf_token', '')
            received = request.headers.get('X-CSRF-Token', '')
            origin = os.environ.get('ORANGE_PUBLIC_ORIGIN', request.host_url.rstrip('/'))
            if not expected or not hmac.compare_digest(expected.encode(), received.encode()) or request.headers.get('Origin') != origin:
                return jsonify(error='安全校验失败，请刷新后台重试'), 403
        try:
            key = os.environ.get('MODERATION_BRIDGE_SECRET') or Path(
                os.environ.get('MODERATION_BRIDGE_SECRET_FILE', '/etc/pixel-ybu-moderation-bridge.key')
            ).read_text().strip()
            if len(key) < 32:
                raise ValueError('Missing bridge configuration')
            endpoint = app.config.get('GAME_FEEDBACK_INTERNAL', app.config.get('GAME_MODERATION_INTERNAL', 'http://127.0.0.1:3001')) + '/internal/feedback'
            if route:
                endpoint += '/' + route
            if request.query_string:
                endpoint += '?' + request.query_string.decode('ascii')
            actor = str(app.config.get('ADMIN_USER', '')) or os.environ.get('ORANGE_ADMIN_USER', 'orange')
            req = Request(
                endpoint,
                method=request.method,
                data=request.get_data() if request.method != 'GET' else None,
                headers={
                    'X-Feedback-Key': key,
                    'X-Feedback-Actor': actor.encode('utf-8').hex(),
                    'Content-Type': 'application/json',
                },
            )
            try:
                upstream = urlopen(req, timeout=15)
            except HTTPError as error:
                upstream = error
            with upstream:
                body = upstream.read(4 * 1024 * 1024)
                status = upstream.code
                response = Response(body, status=status, content_type='application/json; charset=utf-8')
                response.headers['Cache-Control'] = 'private, no-store'
                return response
        except (OSError, ValueError, URLError):
            return jsonify(error='游戏反馈服务暂不可用，请稍后重试'), 503
