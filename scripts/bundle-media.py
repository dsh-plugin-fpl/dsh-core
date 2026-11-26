"""Bundle installed macOS FFmpeg tools with their non-system Mach-O dependencies."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
pending = [Path(shutil.which(name) or '') for name in ['ffmpeg', 'ffprobe']]
if any(not p.is_file() for p in pending):
    raise SystemExit('构建机需要 FFmpeg 与 ffprobe，安装包将包含其运行副本。')
seen = {}
manifest = {}
while pending:
    source = pending.pop().resolve()
    if source.name in seen:
        if seen[source.name] != source:
            raise ValueError('媒体动态库名称冲突：' + source.name)
        continue
    seen[source.name] = source
    target = output / source.name
    shutil.copy2(source, target)
    target.chmod(0o755)
    lines = subprocess.check_output(['/usr/bin/otool', '-L', source], text=True).splitlines()[1:]
    dependencies = []
    for line in lines:
        dependency = line.strip().split(' (', 1)[0]
        if dependency.startswith(('/System/', '/usr/lib/')):
            continue
        if dependency == str(source) or dependency == str(source.resolve()):
            continue
        path = Path(dependency)
        if not path.is_absolute() or not path.is_file():
            raise ValueError('无法解析媒体动态库：' + dependency)
        resolved = path.resolve()
        subprocess.run(['/usr/bin/install_name_tool', '-change', dependency,
            '@loader_path/' + resolved.name, target], check=True, capture_output=True)
        dependencies.append(resolved.name)
        pending.append(resolved)
    if target.suffix == '.dylib':
        subprocess.run(['/usr/bin/install_name_tool', '-id', '@loader_path/' + target.name,
            target], check=True, capture_output=True)
    manifest[target.name] = {'dependencies': dependencies}
# install_name_tool invalidates upstream signatures. Resources are not reliably
# discovered by codesign --deep, so sign every relocated binary explicitly.
for name in manifest:
    target = output / name
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', target],
        check=True, capture_output=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--strict', target],
        check=True, capture_output=True)
    manifest[name]['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
(output.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2))
