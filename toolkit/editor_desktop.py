"""Native Windows host for the local editor. No external browser is launched."""
from __future__ import annotations

import threading
from pathlib import Path


class DesktopDialogs:
    def __init__(self):
        self._window = None

    def choose_path(self, kind: str):
        import webview
        if kind not in ('game', 'image', 'dlc', 'output'):
            raise ValueError('Unknown path type')
        mode = webview.FileDialog.OPEN if kind == 'image' else webview.FileDialog.FOLDER
        result = self._window.create_file_dialog(mode, allow_multiple=False)
        return result[0] if result else None


def run_desktop(server, url: str, storage: Path):
    import webview
    webview.settings['ALLOW_DOWNLOADS'] = True
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
    bridge = DesktopDialogs()
    bridge._window = webview.create_window('FH1 Carbin Editor', url, js_api=bridge,
                                          width=1500, height=950, min_size=(1100, 700),
                                          background_color='#101215', text_select=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name='FH1 editor API')
    thread.start()
    try:
        webview.start(gui='edgechromium', private_mode=False, storage_path=str(storage))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
