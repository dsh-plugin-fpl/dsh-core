"""Bounded Skill snapshots; compare content rather than ZIP timestamps."""

import base64
import hashlib
import io
import zipfile

import yaml

from .clients.skill_archive import SkillArchiveStager


def files(encoded):
    if not encoded:
        return {}
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError("Skill ZIP 超过限制")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        if (
            not members
            or len(members) > 1000
            or sum(m.file_size for m in members) > 100 * 1024 * 1024
        ):
            raise ValueError("Skill 文件包超过限制")
        prefix = SkillArchiveStager._root_prefix(members)
        result = {}
        seen = set()
        for member in members:
            SkillArchiveStager._validate_member(member)
            name = member.filename[len(prefix) :]
            if name.casefold() in seen:
                raise ValueError("Skill 路径重复")
            seen.add(name.casefold())
            result[name] = archive.read(member)
        SkillArchiveStager._metadata(result["SKILL.md"])
        return result


def archive_digest(encoded):
    return {name: hashlib.sha256(content).hexdigest() for name, content in files(encoded).items()}


def rename_skill(item, name):
    contents = files(item.archive_base64)
    text = contents["SKILL.md"].decode()
    lines = text.splitlines()
    closing = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    metadata = yaml.safe_load("\n".join(lines[1:closing]))
    metadata["name"] = name
    contents["SKILL.md"] = (
        "---\n"
        + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
        + "---\n"
        + "\n".join(lines[closing + 1 :])
    ).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(contents.items()):
            entry = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, content)
    return item.model_copy(
        update={
            "name": name,
            "config": {**item.config, "name": name},
            "archive_base64": base64.b64encode(output.getvalue()).decode(),
        }
    )
