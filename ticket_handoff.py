"""Private launcher adapter: read ticket JSON from stdin and POST from the browser.

The short-lived local HTML is removed after 90 seconds. No bearer URL is opened.
"""
import argparse
from html import escape
import ipaddress
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import urlsplit
import webbrowser


def handoff_html(url, payload):
    parts=urlsplit(url)
    try:
        loopback=ipaddress.ip_address(parts.hostname or '').is_loopback
    except ValueError:
        loopback=(parts.hostname=='localhost')
    if (parts.scheme!='https' and not (parts.scheme=='http' and loopback)) or parts.username or parts.password or parts.query or parts.fragment or parts.path!='/admin/quick-login':
        raise ValueError('Use HTTPS or a loopback HTTP admin ticket endpoint, without query or credentials.')
    ticket=payload.get('ticket')
    if not isinstance(ticket,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',ticket):
        raise ValueError('Invalid ticket response.')
    return ('<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
            '<title>管理员快捷登录</title><form method="post" action="'+escape(url,quote=True)+'">'
            '<input type="hidden" name="ticket" value="'+escape(ticket,quote=True)+'">'
            '<button type="submit">继续登录</button></form><script>document.forms[0].submit()</script>')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--consume-url', required=True)
    args=p.parse_args()
    try:
        document=handoff_html(args.consume_url,json.loads(sys.stdin.read(4097)))
    except (ValueError,AttributeError):
        p.exit(1,'Invalid consume URL or issuer response.\n')
    with tempfile.TemporaryDirectory(prefix='orange-admin-handoff-') as folder:
        page=Path(folder)/'login.html'
        page.write_text(document,encoding='utf-8')
        page.chmod(0o600)
        if not webbrowser.open(page.as_uri()):
            p.exit(1,'Unable to open browser.\n')
        # This is a user-launched helper, not a web worker or Codex wait.
        time.sleep(90)
