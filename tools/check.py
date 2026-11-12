"""Checks registry.json. Runs on every pull request (.github/workflows/check.yml) and works locally the same way:

    python tools/check.py            # official plugins are rebuilt from their folders, others are downloaded
    python tools/check.py --download # also download the official zips (after their release is up)

For every entry: the fields are there and valid, the zip's sha256 matches, the zip has nothing outside its folder,
its plugin.json passes the same checks Wavelog does before installing, and the entry says the same as that
plugin.json (id, name, version, api, min_app, what it uses). Standard library only.
"""
import hashlib
import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pack import FIELDS, ROOT, pack  # noqa: E402

APIS = {1}
ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
TYPES = {"switch", "text", "number", "slider", "choice"}
USES = {"network", "files", "plays", "games", "overlay", "ducking"}
MAX_SIZE = 20 * 1024 * 1024
REQUIRED = ("id", "name", "author", "description", "version", "api", "min_app", "url", "sha256", "source")


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "wavelog-plugins-check"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(MAX_SIZE + 1)
    if len(data) > MAX_SIZE:
        raise ValueError("the zip is over 20 MB")
    return data


def manifest_problems(m, names):
    """The same rules as read_manifest() in Wavelog's backend/plugins.py."""
    out = []
    if not isinstance(m.get("id"), str) or not ID.match(m["id"]):
        out.append("plugin.json needs an id: 2 to 32 lowercase letters, numbers or dashes")
    if not isinstance(m.get("name"), str) or not m["name"].strip():
        out.append("plugin.json needs a name")
    if m.get("api", 1) not in APIS:
        out.append(f"plugin.json has api {m.get('api')}, supported: {sorted(APIS)}")
    if not VERSION.match(str(m.get("version", ""))):
        out.append("plugin.json needs a version like 1.0.0")
    if not VERSION.match(str(m.get("min_app", ""))):
        out.append("plugin.json needs min_app, the oldest Wavelog it works with, like 0.10.0")
    if m.get("main", "main.py") not in names:
        out.append(f"{m.get('main', 'main.py')} is missing")
    for s in m.get("settings") or []:
        if not isinstance(s, dict) or not s.get("key") or s.get("type") not in TYPES:
            out.append(f"a setting needs a key and a type ({', '.join(sorted(TYPES))}): {s}")
    unknown = set(m.get("uses") or {}) - USES
    if unknown:
        out.append(f"unknown keys in uses: {', '.join(sorted(unknown))} (known: {', '.join(sorted(USES))})")
    return out


def check_zip(e, data):
    out = []
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return ["the download isn't a zip"]
    names = z.namelist()
    if any(n.startswith(("/", "\\")) or ".." in Path(n).parts or ":" in n for n in names):
        out.append("the zip has files outside its folder")
    tops = {n.split("/", 1)[0] for n in names}
    prefix = f"{next(iter(tops))}/" if len(tops) == 1 and f"{next(iter(tops))}/plugin.json" in names else ""
    if prefix + "plugin.json" not in names:
        return out + ["no plugin.json in the zip (at the top, or in one folder)"]
    try:
        m = json.loads(z.read(prefix + "plugin.json").decode("utf-8"))
    except ValueError as err:
        return out + [f"plugin.json isn't valid JSON: {err}"]
    inner = {n[len(prefix):] for n in names}
    out += manifest_problems(m, inner)
    for k in FIELDS:
        want = m.get(k, 1 if k == "api" else {} if k == "uses" else None)
        if e.get(k, 1 if k == "api" else {} if k == "uses" else None) != want:
            out.append(f"registry {k} doesn't match plugin.json ({e.get(k)!r} vs {want!r})")
    return out


def check(online_official=False):
    reg = json.loads((ROOT / "registry.json").read_text("utf-8"))
    problems = []
    if reg.get("registry") != 1 or not isinstance(reg.get("plugins"), list):
        return ['registry.json needs {"registry": 1, "plugins": [...]}']
    seen = set()
    for e in reg["plugins"]:
        pid = e.get("id", "?")
        bad = [f"missing {k}" for k in REQUIRED if k not in e]
        if pid in seen:
            bad.append("the id is used twice")
        seen.add(pid)
        if not re.fullmatch(r"[0-9a-f]{64}", str(e.get("sha256", ""))):
            bad.append("sha256 must be 64 lowercase hex characters")
        for k in ("url", "source", "icon"):
            if k in e and not str(e[k]).startswith("https://"):
                bad.append(f"{k} must be an https link")
        if e.get("official") and not (ROOT / pid / "plugin.json").exists():
            bad.append("official plugins live in a folder of this repo")
        if not bad:
            try:
                if e.get("official") and not online_official:
                    data = pack(ROOT / pid)
                    where = "rebuilt from its folder"
                else:
                    data = download(e["url"])
                    where = "downloaded"
                got = hashlib.sha256(data).hexdigest()
                if got != e["sha256"]:
                    bad.append(f"sha256 of the zip ({where}) is {got}, the registry says {e['sha256']}"
                               + (". Run python tools/pack.py " + pid if e.get("official") else ""))
                else:
                    bad += check_zip(e, data)
            except Exception as err:
                bad.append(f"couldn't get the zip: {err}")
        problems += [f"{pid}: {b}" for b in bad]
        print(f"{'ok  ' if not bad else 'FAIL'} {pid} {e.get('version', '')}")
    return problems


if __name__ == "__main__":
    found = check("--download" in sys.argv)
    for p in found:
        print("  " + p)
    sys.exit(1 if found else 0)
