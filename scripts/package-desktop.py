"""Assemble a self-contained macOS arm64 candidate from locked product artifacts."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--product', type=Path, required=True)
parser.add_argument('--module', required=True)
parser.add_argument('--application-id', required=True)
parser.add_argument('--title', required=True)
parser.add_argument('--path', required=True)
parser.add_argument('--port', type=int, required=True)
parser.add_argument('--dmg', action='store_true')
options = parser.parse_args()
if platform.system() != 'Darwin' or platform.machine() != 'arm64':
    parser.error('首轮桌面构建只支持 macOS Apple Silicon')
core = Path(__file__).resolve().parents[1]
product = options.product.resolve()
web = product / '.runtime/web'
if not (web / 'index.html').is_file() or not (web / 'assets').is_dir():
    raise SystemExit('请先完成产品界面构建，再生成桌面候选。')

def web_fingerprint(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob('*') if path.is_file()}

web_snapshot = web_fingerprint(web)
version = json.loads((product / 'desktop/package.json').read_text())['version']
stage = product / '.runtime/desktop' / version
if stage.exists():
    raise SystemExit('构建目录已存在；请显式归档旧候选或使用新的版本号。')
stage.mkdir(parents=True)
env = {**os.environ, 'UV_CACHE_DIR': str(product / '.runtime/uv-cache')}

def run(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, env=env, **kwargs)

# Build wheels rather than shipping editable links to development repositories.
wheels = stage / 'wheels'
run('uv', 'build', '--wheel', '--project', core / 'python', '--out-dir', wheels)
run('uv', 'build', '--wheel', '--project', product, '--out-dir', wheels)
requirements = stage / 'requirements.txt'
with requirements.open('w') as stream:
    run('uv', 'export', '--project', product, '--frozen', '--no-dev', '--no-emit-project',
        '--no-emit-package', 'fpl-core', '--no-hashes', '--format', 'requirements-txt', stdout=stream)
app = stage / (options.title + '.app')
shutil.copytree(product / 'desktop/node_modules/electron/dist/Electron.app', app, symlinks=True)
resources = app / 'Contents/Resources'
info_path = app / 'Contents/Info.plist'
with info_path.open('rb') as stream:
    info = plistlib.load(stream)
info.update(CFBundleName=options.title, CFBundleDisplayName=options.title,
    CFBundleIdentifier='lab.futureprops.' + options.application_id,
    CFBundleVersion=version, CFBundleShortVersionString=version)
with info_path.open('wb') as stream:
    plistlib.dump(info, stream)
# uv-managed Python is relocatable; reject a system or Homebrew interpreter.
python = product / '.runtime/venv/bin/python'
base = Path(subprocess.check_output([python, '-c', 'import sys; print(sys.base_prefix)'], text=True).strip())
if not (base / 'PYTHON.json').exists() and 'cpython-' not in base.name:
    raise SystemExit('桌面构建需要 uv 管理的可搬移 Python。')
shutil.copytree(base, resources / 'python', symlinks=True,
    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'test', 'tests', 'site-packages'))
packaged_python = resources / 'python/bin/python3'
minor = subprocess.check_output([packaged_python, '-c', 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}")'], text=True).strip()
site = resources / 'python/lib' / minor / 'site-packages'
run('uv', 'pip', 'install', '--python', packaged_python, '--target', site,
    '--requirements', requirements, *sorted(wheels.glob('*.whl')))
shutil.copytree(web, resources / 'web')
if web_fingerprint(resources / 'web') != web_snapshot or web_fingerprint(web) != web_snapshot:
    raise SystemExit('桌面组装期间界面产物发生变化，请等待构建结束后使用新候选版本重试。')
shutil.copytree(product / 'src', resources / 'product-assets',
    ignore=shutil.ignore_patterns('runtime', '*.mjs', '*.json', '*.md'))
application = resources / 'app'
application.mkdir()
shutil.copytree(core / 'packages/desktop-runtime', application / 'runtime',
    ignore=shutil.ignore_patterns('node_modules'))
(application / 'package.json').write_text(json.dumps({'name': options.application_id,
    'version': version, 'type': 'module', 'main': 'main.mjs'}))
config = {'title': options.title, 'applicationId': options.application_id,
    'port': options.port, 'path': options.path}
(application / 'main.mjs').write_text('''import {runDesktop} from './runtime/index.mjs';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
const resources = fileURLToPath(new URL('../', import.meta.url));
void runDesktop({...CONFIG,
 icon: resolve(resources, 'product-assets/icon.png'),
 python: resolve(resources, 'python/bin/python3'), cwd: resources,
 args: ['-m', MODULE, 'desktop', '--web-root', resolve(resources, 'web')],
 env: {PYTHONNOUSERSITE: '1', PYTHONPATH: '', FPL_APP_RESOURCES: resolve(resources, 'product-assets'),
 PATH: resolve(resources, 'media/bin') + ':' + (process.env.PATH || '/usr/bin:/bin')},
});
'''.replace('CONFIG', json.dumps(config)).replace('MODULE', json.dumps(options.module + '.cli')))
def source_fingerprint(repository):
    names = subprocess.check_output(['git', '-C', str(repository), 'ls-files', '-co', '--exclude-standard', '-z'], text=True).split('\0')
    digest = hashlib.sha256()
    for name in sorted(set(names)):
        path = repository / name
        if name and path.is_file():
            digest.update(name.encode() + b'\0' + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()

for metadata in site.glob('*.dist-info/direct_url.json'):
    metadata.unlink()
manifest = {'version': version, 'platform': 'darwin-arm64', 'application_id': options.application_id,
    'source_fingerprints': {'core': source_fingerprint(core), 'product': source_fingerprint(product)},
    'wheels': {wheel.name: hashlib.sha256(wheel.read_bytes()).hexdigest() for wheel in wheels.glob('*.whl')}}
(resources / 'build-manifest.json').write_text(json.dumps(manifest, indent=2))
run(sys.executable, core / 'scripts/bundle-media.py', resources / 'media/bin')
# This is a local candidate signature, not Developer ID notarization.
run('/usr/bin/codesign', '--force', '--deep', '--sign', '-', app)
run('/usr/bin/codesign', '--verify', '--deep', '--strict', app)
if options.dmg:
    run('/usr/bin/hdiutil', 'create', '-volname', options.title, '-srcfolder', app,
        '-format', 'UDZO', stage / (options.title + '.dmg'))
print(json.dumps({'application': str(app), 'version': version, 'notarized': False}, ensure_ascii=False))
