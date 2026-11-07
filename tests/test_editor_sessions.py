import http.cookiejar
import importlib.util
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path


def test_opening_second_editor_keeps_first_library_accessible():
    """Native windows share a WebView profile; cookies ignore the TCP port."""
    servers = []
    threads = []
    modules = []
    root = Path(__file__).resolve().parents[1]
    try:
        for index in range(2):
            spec = importlib.util.spec_from_file_location(f'isolated_editor_{index}', root / 'toolkit/carbin_editor/server.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.TOKEN = f'editor-session-{index}'
            module.S.cfg = {'game': f'saved-game-{index}', 'dlc': []}
            httpd = module.ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            modules.append(module)
            servers.append(httpd)
            threads.append(thread)
        browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        urls = [f'http://127.0.0.1:{httpd.server_port}' for httpd in servers]
        for url, module in zip(urls, modules):
            browser.open(url + '/?token=' + module.TOKEN).read()
        for index, url in enumerate(urls):
            try:
                response = browser.open(url + '/api/library')
            except urllib.error.HTTPError as exc:
                raise AssertionError(f'Editor {index} lost access to its remembered game after a second window opened: HTTP {exc.code}') from exc
            assert json.loads(response.read())['config']['game'] == f'saved-game-{index}'
    finally:
        for httpd in servers:
            httpd.shutdown()
            httpd.server_close()
        for thread in threads:
            thread.join()
