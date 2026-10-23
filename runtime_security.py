"""Fail closed on missing shared secrets and restrict request authorities."""
import re
import ipaddress
from urllib.parse import urlsplit
from werkzeug.wrappers import Response

DEFAULT_HOSTS = ('orangelab.top', 'www.orangelab.top', '47.117.162.201',
                 'localhost', '127.0.0.1', '[::1]')


def runtime_security_config(environ):
    secret = environ.get('ORANGE_SECRET_KEY', '')
    if len(secret.encode('utf-8')) < 32 or not secret.strip():
        raise RuntimeError('ORANGE_SECRET_KEY must be explicitly configured with at least 32 bytes; value withheld.')
    raw = environ.get('ORANGE_TRUSTED_HOSTS')
    hosts = [v.strip().lower() for v in raw.split(',')] if raw is not None else list(DEFAULT_HOSTS)
    if not hosts or any(not h or len(h)>253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?|\[[0-9a-f:]+\])',h) for h in hosts):
        raise RuntimeError('ORANGE_TRUSTED_HOSTS must contain exact hostnames or bracketed IPv6, without schemes, ports or wildcards.')
    return {'SECRET_KEY': secret, 'SECURITY_TRUSTED_HOSTS': list(dict.fromkeys(hosts))}


class TrustedHostMiddleware:
    """Validate before Flask opens sessions; DNS authority matching ignores case."""
    def __init__(self, application, hosts):
        self.application = application
        self.hosts = {host.strip('[]').lower() for host in hosts}

    def __call__(self, environ, start_response):
        raw = environ.get('HTTP_HOST')
        if raw is None:
            server = environ.get('SERVER_NAME', '')
            raw = '['+server+']' if ':' in server and not server.startswith('[') else server
        try:
            if not re.fullmatch(r'(?:[a-zA-Z0-9.-]+|\[[a-fA-F0-9:.]+\])(?::[0-9]{1,5})?', raw):
                raise ValueError('Malformed authority')
            parsed = urlsplit('//'+raw)
            hostname = parsed.hostname
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError('Invalid port')
            if ':' in hostname:
                ipaddress.IPv6Address(hostname)
            if hostname not in self.hosts:
                raise ValueError('Untrusted authority')
        except (ValueError, TypeError):
            return Response('Invalid request host.', status=400, content_type='text/plain; charset=utf-8',
                            headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})(environ,start_response)
        normalized = environ.copy()
        normalized['HTTP_HOST'] = raw.lower()
        return self.application(normalized,start_response)
