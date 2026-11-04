"""Run a real native desktop window, exercise onboarding, and write observed UI state."""
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'toolkit'))
spec = importlib.util.spec_from_file_location('editor_server', ROOT / 'toolkit/carbin_editor/server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
server.SETTINGS = ROOT / '.runtime-smoke' / 'settings.json'
import webview
from editor_desktop import DesktopDialogs

output = ROOT / 'native-smoke.json'
httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
url = f'http://127.0.0.1:{httpd.server_port}/?token={server.TOKEN}'
bridge = DesktopDialogs()
window = webview.create_window('FH1 Carbin Editor — native smoke test', url, width=1500, height=950, js_api=bridge)
bridge._window = window


def probe():
    result = {}
    def evaluate(script):
        return window.evaluate_js(script)

    def wait_for(script, seconds=45):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = evaluate(script)
            if value:
                return value
            time.sleep(0.2)
        raise AssertionError({'waiting_for': script, 'status': evaluate('document.querySelector("#status").textContent'), 'errors': evaluate('window.editorErrors')})

    try:
        for _ in range(100):
            time.sleep(0.2)
            result = window.evaluate_js('({status:document.querySelector("#status")?.textContent,errors:window.editorErrors,settingsVisible:document.querySelector("#tab-settings")?.hidden === false,canvas:!!document.querySelector("#gl")?.getContext("webgl2")})') or {}
            if result.get('errors'):
                break
            if result.get('settingsVisible'):
                break
        assert result.get('settingsVisible'), result
        assert result.get('canvas'), result
        wait_for('!!window.pywebview?.api?.choose_path')
        result['native_api'] = evaluate('Object.keys(window.pywebview.api)')
        assert result['native_api'] == ['choose_path'], result['native_api']
        game = 'C:/Users/ryan/Downloads/Forza Modding Development/Assets and refrences/inputs/4D5309C9/00007000/DD04A22D0F8E35E0C3EE744E2B7A1346'
        dlc = 'C:/Users/ryan/Downloads/xenia_canary_windows/content/0000000000000000/4D5309C9/00000002'
        evaluate(f'document.querySelector("#cfgGame").value={json.dumps(game)}; document.querySelector("#cfgDlc").value={json.dumps(dlc)}; document.querySelector("#applySettings").click()')
        wait_for('document.querySelectorAll(".car-row").length > 176', 120)
        result['cars'] = evaluate('document.querySelectorAll(".car-row").length')
        evaluate('document.querySelector("#carSearch").value="ALF_8C";document.querySelector("#carSearch").dispatchEvent(new Event("input"));document.querySelector(".car-row").dispatchEvent(new MouseEvent("contextmenu", {bubbles:true,clientX:1100,clientY:350}));document.querySelector("#carContextMenu button").click()')
        wait_for('document.querySelectorAll("#tree .sec").length > 0')
        with server.S.lock:
            original_sections = len(server.S.carbin.sections)
        evaluate('document.querySelector("#tree .sec .row").click();document.querySelector("#trx").value="0.1";[...document.querySelectorAll("#tab-part button")].find(b=>b.textContent==="Apply").click()')
        wait_for('document.querySelector("#status").textContent.includes("updated")')
        with server.S.lock:
            edited = server.fc.build(server.S.carbin)
        evaluate('document.querySelector("#btnCars").click();document.querySelector("#carSearch").value="";document.querySelector("#carSearch").dispatchEvent(new Event("input"));document.querySelectorAll(".car-row")[1].dispatchEvent(new MouseEvent("contextmenu", {bubbles:true,clientX:1100,clientY:350}));document.querySelectorAll("#carContextMenu button")[1].click()')
        wait_for('document.querySelectorAll(".donor-row").length > 0')
        evaluate('document.querySelector(".donor-row button").click()')
        wait_for('!!document.querySelector("#pastePart")')
        with server.S.lock:
            assert server.fc.build(server.S.carbin) == edited
        evaluate('document.querySelector("#pastePart").click()')
        wait_for(f'document.querySelectorAll("#tree .sec").length === {original_sections + 1}')
        result['donor_copy_and_paste'] = True
        evaluate('document.querySelector("#btnUndo").click()')
        wait_for(f'document.querySelectorAll("#tree .sec").length === {original_sections}')
        evaluate('document.querySelector("#btnRedo").click()')
        wait_for(f'document.querySelectorAll("#tree .sec").length === {original_sections + 1}')
        result['undo_redo'] = True
        evaluate('document.querySelector("#tree .sec .row").click(); const data = new DataTransfer(); data.items.add(new File(["o CustomTriangle\\nv 0 1 0\\nv 0.2 1 0\\nv 0 1.2 0\\nusemtl body\\nf 1 2 3\\n"],"DesktopTest.obj",{type:"text/plain"})); window.dispatchEvent(new DragEvent("drop",{dataTransfer:data,bubbles:true,cancelable:true}))')
        wait_for('!!document.querySelector("#mpx")')
        evaluate('document.querySelector("#mpy").value="1.5";document.querySelector("#mpy").dispatchEvent(new Event("change"));[...document.querySelectorAll("#tab-model button")].find(b=>b.textContent==="Add to car").click()')
        wait_for('!document.querySelector("#mpx")')
        with server.S.lock:
            assert not server.cg.validate(server.S.carbin)
        result['custom_obj_placement'] = True
        result['errors'] = evaluate('window.editorErrors')
        assert not result['errors'], result
        result['passed'] = True
    except Exception as exc:
        result['error'] = repr(exc)
    finally:
        output.write_text(json.dumps(result, indent=2))
        time.sleep(2)
        window.destroy()


try:
    webview.start(probe, gui='edgechromium')
finally:
    httpd.shutdown()
    httpd.server_close()
print(output.read_text())
if not json.loads(output.read_text()).get('passed'):
    raise SystemExit(1)
