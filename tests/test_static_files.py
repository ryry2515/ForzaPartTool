"""Every file the page imports must be served by the editor and copied into the packaged program.
A new module that is missing from either list makes the whole page fail to load (the server answers 404)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "toolkit" / "carbin_editor"


def local_imports(path):
    text = path.read_text(encoding="utf-8")
    return {m.group(1) for m in re.finditer(r"""from\s+['"]\./([A-Za-z0-9_]+\.m?js)['"]""", text)}


def page_modules():
    seen, todo = set(), ["app.js"]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo.extend(local_imports(EDITOR / name))
    return seen


def test_modules_are_served_by_the_editor():
    served = (EDITOR / "server.py").read_text(encoding="utf-8")
    missing = [name for name in page_modules() if f'"/{name}"' not in served]
    assert not missing, f"not in the server's static list: {missing}"


def test_modules_are_packaged():
    build = (ROOT / "packaging" / "build_windows.py").read_text(encoding="utf-8")
    missing = [name for name in page_modules() if f'"{name}"' not in build]
    assert not missing, f"not in packaging/build_windows.py: {missing}"
