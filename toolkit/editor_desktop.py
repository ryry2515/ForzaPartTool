"""Native Windows host for the local editor. No external browser is launched."""
from __future__ import annotations

import threading
from pathlib import Path


class DesktopDialogs:
    def __init__(self):
        self._window = None

    def choose_path(self, kind: str, start: str = ''):
        import webview
        if kind not in ('game', 'image', 'dlc', 'output'):
            raise ValueError('Unknown path type')
        mode = webview.FileDialog.OPEN if kind == 'image' else webview.FileDialog.FOLDER
        folder = ''
        if isinstance(start, str) and start.strip():          # start where the last choice was
            candidate = Path(start.strip().split(';')[-1]).expanduser()
            while candidate != candidate.parent and not candidate.is_dir():
                candidate = candidate.parent
            folder = str(candidate) if candidate.is_dir() else ''
        result = self._window.create_file_dialog(mode, directory=folder, allow_multiple=False)
        return result[0] if result else None

    def copy_text(self, text: str):
        from System import Action
        from System.Windows.Forms import Clipboard
        if not isinstance(text, str) or len(text) > 262144:
            raise ValueError('Clipboard text is invalid or too long')
        self._window.native.Invoke(Action(lambda: Clipboard.SetText(text)))
        return True


def run_desktop(server, url: str, storage: Path):
    import webview
    webview.settings['ALLOW_DOWNLOADS'] = True
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
    bridge = DesktopDialogs()
    bridge._window = webview.create_window('FH1 Carbin Editor v2 — test build 16', url, js_api=bridge,
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
