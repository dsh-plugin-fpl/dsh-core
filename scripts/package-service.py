"""Package this provider's production service; never include state or local artwork."""
import hashlib
import json
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
config = json.loads((root / "service.config.json").read_text())
pkg = json.loads((root / "package.json").read_text())
core = config["id"] == "core"
web = root / ("web/dist" if core else ".runtime/web")
if not (web / "index.html").is_file():
    raise SystemExit("Build the provider web application before packaging")
output = root / ".runtime/service-package"
output.mkdir(parents=True, exist_ok=True)
files = {}

def add(source, target):
    if source.is_symlink():
        raise ValueError("Service payload refuses symlinks: " + str(source.relative_to(root)))
    files[target] = source.read_bytes()

def tree(source, target):
    for path in sorted(source.rglob("*")):
        if any(p in {"__pycache__", "node_modules", ".local", ".pytest_cache", ".runtime"} for p in path.relative_to(source).parts):
            continue
        if path.is_file() and not path.name.endswith((".pyc", ".pyo")):
            add(path, target + "/" + path.relative_to(source).as_posix())

tree(root / "python" / config["module"], "python/" + config["module"])
for name in ["pyproject.toml", "uv.lock"]:
    add(root / ("python" if core else "") / name, ("python/" if core else "") + name)
if core:
    add(root / "scripts/core.mjs", "scripts/core.mjs")
else:
    tree(root / "contracts", "contracts")
    for name in ["application.mjs", "launcher-lock.mjs"]:
        add(root / "scripts" / name, "scripts/" + name)
    add(root / "src/icon.svg", "src/icon.svg")
tree(web, "web/dist" if core else ".runtime/web")
files["package.json"] = (json.dumps({"name": pkg["name"], "version": pkg["version"], "type": "module", "private": True}) + "\n").encode()
archive = output / "service.zip"
with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
    for name, data in sorted(files.items()):
        info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100600 << 16
        bundle.writestr(info, data)
manifest = {"protocol": 1, **config, "version": pkg["version"], "package": pkg["name"], "platforms": ["darwin-arm64", "darwin-x64"], "python": "3.13.15", "payload": "service.zip", "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "byte_size": archive.stat().st_size, "expanded_size": sum(map(len, files.values())), "files": {k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())}}
(output / "service.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
print("Packaged service " + config["id"] + " " + manifest["sha256"][:12])
