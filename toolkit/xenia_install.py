"""Install a built package folder into Xenia's content directory (folder + Headers/00000002/<name>.header)."""
import os
import shutil
import struct
from pathlib import Path


def xenia_header(name: str, display: str, title_id: int = 0x4D5309C9) -> bytes:
    h = bytearray(332)
    struct.pack_into(">II", h, 0, 1, 2)                      # device id 1, content type 2 (DLC)
    d = display.encode("utf-16-be")[:254]
    h[0x8:0x8 + len(d)] = d
    n = name.encode("ascii")[:42]
    h[0x108:0x108 + len(n)] = n
    struct.pack_into(">I", h, 0x140, title_id)
    return bytes(h)


def _link_or_copy(src, dst):
    """Hard-link on the same drive (instant, no second 4.8 GB on disk); copy across drives. fh1_pack writes
    fresh files every build, so a link never sees a later build's changes."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return dst


def install(package_folder: Path, xenia_content_root: Path, display: str, title_id: int = 0x4D5309C9) -> Path:
    """xenia_content_root: .../content/0000000000000000 (or the xenia folder itself)."""
    root = Path(xenia_content_root)
    if (root / "content").is_dir():
        root = root / "content" / "0000000000000000"
    title = root / f"{title_id:08X}"
    name = Path(package_folder).name
    dest = title / "00000002" / name
    if dest.exists():
        # Move the old copy aside first: Windows refuses the rename while Xenia has a file open, so a
        # running game makes us stop here instead of half-deleting the installed package.
        old = dest.with_name(name + ".old")
        if old.exists():
            shutil.rmtree(old)
        try:
            dest.rename(old)
        except PermissionError:
            raise RuntimeError(f"{name} is in use - close Xenia, then install again") from None
        shutil.rmtree(old)
    shutil.copytree(package_folder, dest, copy_function=_link_or_copy)
    hdr = title / "Headers" / "00000002" / f"{name}.header"
    hdr.parent.mkdir(parents=True, exist_ok=True)
    hdr.write_bytes(xenia_header(name, display, title_id))
    return dest
