"""Loopback-only desktop web shell. Every API request requires a launch token."""
import argparse
import json
import os
import secrets
import subprocess
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .controller import Controller
from .paths import ROOT, DATA

WEB = ROOT / 'ticketdesk/web'


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, controller=None):
        super().__init__(address, Handler)
        self.controller = controller or Controller()
        self.token = secrets.token_urlsafe(32)
        self.authority = f'127.0.0.1:{self.server_port}'
        self.origin = 'http://' + self.authority


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, data, content_type='application/json; charset=utf-8'):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        if self.headers.get('Host') != self.server.authority:
            return False
        origin = self.headers.get('Origin')
        if origin and origin != self.server.origin:
            return False
        return secrets.compare_digest(self.headers.get('X-App-Token', ''), self.server.token)

    def do_GET(self):
        if self.headers.get('Host') != self.server.authority:
            self.reply(403, {'error': 'Host rejected'})
            return
        static = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if self.path in static:
            name, content_type = static[self.path]
            self.reply(200, (WEB/name).read_bytes(), content_type)
            return
        if not self.authorized():
            self.reply(403, {'error': '请从桌面启动入口打开应用。'})
            return
        if self.path == '/api/state':
            self.server.controller.last_seen = time.monotonic()
            self.reply(200, self.server.controller.snapshot())
        elif self.path == '/api/report':
            self.reply(200, self.server.controller.report())
        elif self.path == '/api/comparison':
            self.reply(200, {'text': (ROOT/'docs/项目对比与选型.md').read_text(encoding='utf-8')})
        else:
            self.reply(404, {'error': 'Not found'})

    def do_POST(self):
        if not self.authorized():
            self.reply(403, {'error': '请求来源或启动令牌无效。'})
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 <= length <= 4096:
                raise ValueError('请求过大')
            body = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(body, dict):
                raise ValueError('请求格式错误')
            controller = self.server.controller
            if self.path == '/api/start':
                controller.start(body.get('mode', 'simulation'), body.get('scenario', 'captcha'))
            elif self.path == '/api/autox/open':
                controller.open_autox()
            elif self.path == '/api/autox/rehearsal/open':
                controller.open_autox_rehearsal()
            elif self.path == '/api/profile/save':
                self.reply(200, {'profile': controller.save_profile(body)})
                return
            elif self.path == '/api/profile/select':
                controller.select_profile(body.get('id'))
            elif self.path == '/api/stop':
                controller.stop()
            elif self.path == '/api/verify':
                controller.verify()
            elif self.path == '/api/quit':
                controller.stop()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.reply(404, {'error': 'Not found'})
                return
            self.reply(200, {'ok': True})
        except (ValueError, TypeError) as exc:
            self.reply(400, {'error': str(exc)})
        except Exception:
            self.reply(500, {'error': '服务执行失败，请查看运行日志。'})


def open_app(url):
    candidates = [Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe',
                  Path(os.environ.get('PROGRAMFILES', 'C:/Program Files'))/'Microsoft/Edge/Application/msedge.exe']
    for browser in candidates:
        if browser.exists():
            subprocess.Popen([str(browser), '--app='+url, '--window-size=1280,900'])
            return
    webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    state_file = DATA/'server.json'
    if not args.no_browser:
        try:
            previous = json.loads(state_file.read_text(encoding='utf-8'))
            url = previous['url']
            # Only ever reconnect to loopback, even if a local file was edited.
            from urllib.parse import urlsplit
            parsed = urlsplit(url)
            if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1':
                raise ValueError('invalid previous address')
            req = urllib.request.Request(url+'/api/state', headers={'X-App-Token': previous['token']})
            with urllib.request.urlopen(req, timeout=1) as response:
                json.load(response)
            open_app(url+'/#'+previous['token'])
            return
        except Exception:
            pass
    server = AppServer(('127.0.0.1', args.port))
    metadata = {'url': server.origin, 'token': server.token, 'pid': os.getpid()}
    state_file.write_text(json.dumps(metadata), encoding='utf-8')
    if not args.no_browser:
        open_app(server.origin+'/#'+server.token)
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        server.controller.stop()
        if server.controller.thread is not None:
            server.controller.thread.join(timeout=3)
        server.server_close()
        try:
            if json.loads(state_file.read_text(encoding='utf-8')).get('pid') == os.getpid():
                state_file.unlink()
        except (OSError, ValueError):
            pass


if __name__ == '__main__':
    main()
