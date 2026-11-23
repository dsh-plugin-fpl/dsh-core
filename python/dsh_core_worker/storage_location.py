"""Public DSH-FPL storage locator v1. Contains paths only, never credentials.

This dependency-free contract is distributed with each independent service.
Explicit CLI roots intentionally bypass machine configuration for isolation.
"""

import json
import os
import re
from pathlib import Path

DEFAULT_ROOT = Path.home() / "Library/Application Support/DSH-FPL"
DEFAULT_LOCATOR = Path.home() / "Library/Preferences/dsh-fpl-storage.json"


def locator_path():
    return Path(os.environ.get("DSH_FPL_STORAGE_CONFIG", str(DEFAULT_LOCATOR))).expanduser()


def read_location():
    path = locator_path()
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("存储定位文件必须是仅当前用户可读的普通文件")
    value = json.loads(path.read_text())
    if value.get("version") != 1 or not isinstance(value.get("revision"), int):
        raise ValueError("不支持的存储定位版本")
    root = Path(value["root"])
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("已配置的存储目录不可访问；没有创建空数据替代它")
    marker = root / ".dsh-fpl-storage.json"
    if not marker.is_file() or marker.is_symlink():
        raise ValueError("存储目录缺少所属实例标识")
    identity = json.loads(marker.read_text())
    if identity.get("instance") != value.get("instance"):
        raise ValueError("存储目录实例标识不匹配")
    return value


def root_for(application, legacy, explicit=None):
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    value = read_location()
    if value is None:
        return Path(legacy).resolve()
    if application != "core" and not re.fullmatch(r"dsh-[a-z0-9]+(?:-[a-z0-9]+)*", application):
        raise ValueError("应用存储 ID 无效")
    root = Path(value["root"]) / ("core" if application == "core" else "workshops/" + application)
    if not root.is_dir():
        raise ValueError("应用尚未在统一存储中初始化，请使用显式安装入口")
    return root


def new_application_root(application):
    value = read_location()
    if value is None:
        return None
    if not re.fullmatch(r"dsh-[a-z0-9]+(?:-[a-z0-9]+)*", application):
        raise ValueError("应用存储 ID 无效")
    return Path(value["root"]) / "workshops" / application
