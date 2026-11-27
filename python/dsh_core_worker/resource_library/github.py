"""Bounded anonymous GitHub reads. Every file is verified against a pinned Git tree."""

import hashlib
import json
import re
from urllib.parse import quote, unquote, urlsplit

import httpx
from fastapi import HTTPException

MAX_FILE = 64 * 1024 * 1024
MAX_TREE = 30000
FORBIDDEN = re.compile(
    r"(^|/)(\.git|node_modules|\.runtime|\.local|\.env[^/]*|\.npmrc|"
    r"credentials?[^/]*|secrets?\.json|id_rsa|id_ed25519|[^/]*\.(pem|key))($|/)",
    re.IGNORECASE,
)


def safe_path(value, empty=False):
    if empty and value == "":
        return value
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
        or any(p in {"", ".", ".."} for p in value.split("/"))
        or ":" in value
        or FORBIDDEN.search(value)
    ):
        raise ValueError("资源包含不允许的文件路径")
    return value


def parse_url(value, ref=""):
    url = urlsplit(value.strip())
    if url.scheme != "https" or url.netloc.lower() != "github.com" or url.query or url.fragment:
        raise ValueError("请输入公开的 https://github.com/owner/repository 链接")
    parts = unquote(url.path).strip("/").split("/")
    if len(parts) < 2 or not all(re.fullmatch(r"[\w.-]+", v, re.ASCII) for v in parts[:2]):
        raise ValueError("GitHub 仓库地址无效")
    owner, repo = parts[:2]
    repo = repo.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", owner) or repo in {"", ".", ".."}:
        raise ValueError("GitHub 仓库地址无效")
    path = ""
    if len(parts) > 2:
        if parts[2] not in {"tree", "blob"} or len(parts) < 4:
            raise ValueError("请使用仓库、tree 目录或 SKILL.md 文件链接")
        rest = "/".join(parts[3:])
        if ref:
            if rest != ref and not rest.startswith(ref + "/"):
                raise ValueError("链接中的分支与指定分支不一致")
            path = rest[len(ref) :].lstrip("/")
        else:
            ref, path = parts[3], "/".join(parts[4:])
        if parts[2] == "blob":
            if path.rsplit("/", 1)[-1] != "SKILL.md":
                raise ValueError("文件链接必须指向 SKILL.md")
            path = path.removesuffix("SKILL.md").rstrip("/")
    safe_path(path, empty=True)
    if len(ref) > 240 or any(c in ref for c in "\x00\r\n"):
        raise ValueError("GitHub 分支名称无效")
    return {"repository": f"{owner}/{repo}", "ref": ref, "path": path}


class GitHub:
    def __init__(self, transport=None):
        self.transport = transport

    async def read(self, url, limit=MAX_FILE):
        # URLs are constructed from validated repository identifiers and paths only.
        try:
            async with (
                httpx.AsyncClient(
                    transport=self.transport,
                    timeout=30,
                    follow_redirects=False,
                    headers={
                        "User-Agent": "FPL-Resource-Library",
                        "Accept": "application/vnd.github+json",
                    },
                ) as client,
                client.stream("GET", url) as response,
            ):
                if response.status_code in {403, 429}:
                    retry = response.headers.get("retry-after") or response.headers.get(
                        "x-ratelimit-reset", ""
                    )
                    raise HTTPException(
                        429,
                        {
                            "code": "github_rate_limited",
                            "message": "GitHub 暂时限制访问，请稍后刷新；已有缓存可继续浏览。",
                            "retry_after": retry,
                        },
                    )
                if response.status_code == 404:
                    raise HTTPException(
                        404,
                        "仓库、分支或文件不存在；首轮只支持公开仓库。含 / 的分支请填写完整分支名称。",
                    )
                if response.status_code != 200:
                    raise HTTPException(502, "GitHub 读取失败，请稍后重试")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > limit:
                        raise HTTPException(413, "GitHub 内容超过读取限制")
                return bytes(data)
        except httpx.HTTPError:
            raise HTTPException(
                502, "无法连接 GitHub；请检查网络后重试，已有缓存仍可浏览。"
            ) from None

    async def api(self, path):
        try:
            return json.loads(await self.read("https://api.github.com" + path, 12 * 1024 * 1024))
        except (ValueError, TypeError):
            raise HTTPException(502, "GitHub 返回了无效数据") from None

    async def snapshot(self, locator):
        repository, ref = locator["repository"], locator["ref"]
        if not ref:
            info = await self.api("/repos/" + repository)
            if info.get("private"):
                raise HTTPException(422, "首轮仅支持公开 GitHub 仓库")
            ref = info["default_branch"]
        commit = await self.api(f"/repos/{repository}/commits/{quote(ref, safe='')}")
        revision = commit["sha"]
        if not re.fullmatch("[0-9a-f]{40}", revision):
            raise HTTPException(502, "GitHub 提交标识无效")
        tree = await self.api(
            f"/repos/{repository}/git/trees/{commit['commit']['tree']['sha']}?recursive=1"
        )
        if tree.get("truncated") or len(tree.get("tree", [])) > MAX_TREE:
            raise HTTPException(413, "仓库目录过大，请使用独立资源仓库")
        entries = {row["path"]: row for row in tree["tree"] if row["type"] != "tree"}
        return {**locator, "ref": ref, "revision": revision, "tree": entries}

    async def file(self, snapshot, path, limit=MAX_FILE):
        safe_path(path)
        row = snapshot["tree"].get(path)
        if not row or row.get("mode") not in {"100644", "100755"} or row.get("type") != "blob":
            raise ValueError("资源文件缺失、是符号链接或不是普通文件：" + path)
        if row.get("size", 0) > limit:
            raise HTTPException(413, "资源文件超过大小限制")
        url = f"https://raw.githubusercontent.com/{snapshot['repository']}/{snapshot['revision']}/{quote(path, safe='/')}"
        data = await self.read(url, limit)
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if actual != row["sha"]:
            raise HTTPException(409, "资源内容校验失败，请重新刷新来源")
        if data.startswith(b"version https://git-lfs.github.com/spec/"):
            raise ValueError("首轮暂不支持 Git LFS 文件")
        return data
