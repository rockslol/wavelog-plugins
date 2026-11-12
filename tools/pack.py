"""Packs plugin folders into zips and writes their entries in registry.json.

    python tools/pack.py valorant today      # pack these folders
    python tools/pack.py --all               # pack every folder here that has a plugin.json

A zip is the same bytes every time for the same files (sorted names, a fixed date, no compression, line endings
turned into \\n), on Windows and on GitHub's Linux runners alike. That's what lets tools/check.py rebuild an
official plugin from its folder and compare the sha256 with the one in registry.json.

Zips go to dist/<id>-<version>.zip. Upload them to a GitHub release tagged <id>-<version> in this repo; the entry's
url already points there.
"""
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "rockslol/wavelog-plugins"
TEXT = {".py", ".json", ".svg", ".md", ".txt", ".csv", ".html", ".css", ".js", ".toml", ".cfg", ".ini"}
SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}
STAMP = (1980, 1, 1, 0, 0, 0)
# Copied from plugin.json into the registry entry, so the app can show them before downloading anything.
FIELDS = ("id", "name", "author", "description", "version", "api", "min_app", "uses")


def files(folder):
    out = []
    for f in folder.rglob("*"):
        rel = f.relative_to(folder)
        if f.is_file() and not SKIP_DIRS & set(rel.parts[:-1]) and f.name not in SKIP_FILES and f.suffix != ".pyc":
            out.append((rel.as_posix(), f))
    return sorted(out)


def pack(folder):
    """The zip's bytes. Everything sits inside one <id>/ folder."""
    folder = Path(folder)
    pid = json.loads((folder / "plugin.json").read_text("utf-8"))["id"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for rel, f in files(folder):
            data = f.read_bytes()
            if f.suffix.lower() in TEXT:
                data = data.replace(b"\r\n", b"\n")
            info = zipfile.ZipInfo(f"{pid}/{rel}", STAMP)
            info.create_system = 0
            info.external_attr = 0
            z.writestr(info, data)
    return buf.getvalue()


def entry(folder, data):
    m = json.loads((folder / "plugin.json").read_text("utf-8"))
    e = {k: m[k] for k in FIELDS if k in m}
    e.setdefault("api", 1)
    e.setdefault("uses", {})
    tag = f"{m['id']}-{m['version']}"
    e["url"] = f"https://github.com/{REPO}/releases/download/{tag}/{tag}.zip"
    e["sha256"] = hashlib.sha256(data).hexdigest()
    e["size"] = len(data)
    e["source"] = f"https://github.com/{REPO}/tree/main/{folder.name}"
    if m.get("icon"):
        e["icon"] = f"https://raw.githubusercontent.com/{REPO}/main/{folder.name}/{m['icon']}"
    e["official"] = True
    return e


def main(args):
    folders = [p for p in sorted(ROOT.iterdir()) if (p / "plugin.json").exists()] if "--all" in args else [ROOT / a for a in args]
    if not folders:
        raise SystemExit(__doc__)
    reg_file = ROOT / "registry.json"
    reg = json.loads(reg_file.read_text("utf-8")) if reg_file.exists() else {"registry": 1, "plugins": []}
    (ROOT / "dist").mkdir(exist_ok=True)
    for folder in folders:
        if not (folder / "plugin.json").exists():
            raise SystemExit(f"{folder} has no plugin.json")
        data = pack(folder)
        e = entry(folder, data)
        (ROOT / "dist" / f"{e['id']}-{e['version']}.zip").write_bytes(data)
        reg["plugins"] = [p for p in reg["plugins"] if p["id"] != e["id"]] + [e]
        print(f"{e['id']} {e['version']}  {e['sha256']}  {len(data)} bytes")
    reg["plugins"].sort(key=lambda p: (not p.get("official"), p["name"].lower()))
    reg_file.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", "utf-8", newline="\n")


if __name__ == "__main__":
    main(sys.argv[1:])
