"""Verify closing the actual desktop window stops its API server."""
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'toolkit'))
import editor_desktop
import webview

server = ThreadingHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
port = server.server_port
original_start = webview.start


def start_with_close(**kwargs):
    def close_window():
        time.sleep(1)
        webview.windows[0].destroy()
    return original_start(close_window, **kwargs)


webview.start = start_with_close
editor_desktop.run_desktop(server, f'http://127.0.0.1:{port}/', Path(__file__).resolve().parents[1] / '.runtime-smoke/lifecycle-profile')
with socket.socket() as connection:
    connection.settimeout(1)
    assert connection.connect_ex(('127.0.0.1', port)) != 0, 'API is still listening after window close'
print('Native window closed; backend stopped and port released.')
