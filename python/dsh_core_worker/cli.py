"""Explicit local service lifecycle, independent of the DSH desktop process."""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from urllib.request import ProxyHandler, build_opener
from .private_files import atomic_private


def main():
    repository = Path(__file__).resolve().parents[2]
    if len(sys.argv) > 1 and sys.argv[1].startswith("storage-"):
        from .storage_cli import main as storage_main

        storage_main(repository)
        return
    parser = argparse.ArgumentParser(description="未来道具研究所本机独立服务")
    parser.add_argument(
        "command", choices=["start", "serve", "stop", "status", "dashboard", "start-with-dsh"]
    )
    parser.add_argument("--root", type=Path)
    parser.add_argument("--web-root", type=Path, default=repository / "web/dist")
    parser.add_argument("--port", type=int, default=9310)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--from-plugin", action="store_true", help="插件启动遵守存储维护停写门禁")
    parser.add_argument("--output", type=Path, help="将一次性登录入口写入仅所有者可读的文件")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口必须介于 1024 和 65535")
    from .storage_location import root_for

    root = root_for("core", repository / ".runtime/core", args.root)
    if (
        args.from_plugin
        and args.command == "start"
        and any((parent / ".storage-paused").exists() for parent in (root, *root.parents))
    ):
        raise SystemExit("存储迁移维护中，暂时不能从插件启动服务")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    origin = f"http://127.0.0.1:{args.port}"
    registration = root / "service.json"

    def registered():
        try:
            return json.loads(registration.read_text())
        except (OSError, ValueError):
            return {}

    def probe(endpoint=origin):
        try:
            with build_opener(ProxyHandler({})).open(endpoint + "/ready", timeout=1) as response:
                return json.loads(response.read(1024 * 1024)) if response.status == 200 else {}
        except (OSError, ValueError):
            return {}

    if args.command != "serve":
        import fcntl

        lifecycle_lock = (root / "launcher.lock").open("a")
        fcntl.flock(lifecycle_lock, fcntl.LOCK_EX)
    current, ready = registered(), probe()
    if args.command in ("start", "start-with-dsh", "dashboard") and current.get("port") not in (
        None,
        args.port,
    ):
        previous = probe(f"http://127.0.0.1:{current['port']}")
        if previous.get("instance") == current.get("instance"):
            raise SystemExit(
                f"此运行目录已在端口 {current['port']} 启动，请使用相同端口或指定其他 --root"
            )
    if args.command == "serve":
        import uvicorn

        from .service import create_app

        uvicorn.run(
            create_app(root, args.web_root, origin),
            host="127.0.0.1",
            port=args.port,
            access_log=False,
            ws_max_size=32 * 1024 * 1024,
            log_level="warning",
        )
        return
    if args.command == "status":
        print(
            json.dumps(
                {
                    "running": bool(ready and ready.get("instance") == current.get("instance")),
                    "url": origin,
                    "pid": current.get("pid"),
                },
                ensure_ascii=False,
            )
        )
        return
    if args.command == "stop":
        if not current:
            print("Core 未启动")
            return
        pid = current.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            raise SystemExit("启动登记的 PID 无效，未停止任何进程")

        def process_state():
            try:
                return subprocess.check_output(
                    ["ps", "-p", str(pid), "-o", "stat=", "-o", "command="], text=True
                ).strip()
            except subprocess.CalledProcessError:
                return ""

        line = process_state()
        if not line or line.startswith("Z"):
            if ready:
                raise SystemExit("监听进程与启动登记不一致，未停止任何进程")
            registration.unlink(missing_ok=True)
            print("Core 已停止")
            return
        if f"dsh_core_worker.cli serve --root {root} --web-root " not in line:
            raise SystemExit("PID 不属于此 Core 存储目录，未停止任何进程")
        if ready and ready.get("instance") != current.get("instance"):
            raise SystemExit("启动登记与监听服务不一致，未停止任何进程")
        os.kill(pid, signal.SIGTERM)
        for _ in range(450):
            line = process_state()
            if (not line or line.startswith("Z")) and not probe():
                registration.unlink(missing_ok=True)
                print("Core 已正常停止")
                return
            time.sleep(0.1)
        raise SystemExit("服务仍在正常退出，请稍后检查状态；未强制中断进程")
    if not ready:
        from .access import Access
        with socket.socket() as check:
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                check.bind(("127.0.0.1", args.port))
            except OSError:
                raise SystemExit(f"端口 {args.port} 已被其他程序占用，Core 未启动") from None
        if not (args.web_root / "index.html").is_file():
            raise SystemExit("请先运行 pnpm build:dashboard 构建看板")
        access = Access(root / "access")
        from uuid import uuid4

        instance = str(uuid4())
        atomic_private(root / "instance", instance.encode())
        with (root / "service.log").open("ab") as log:
            os.chmod(root / "service.log", 0o600)
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "dsh_core_worker.cli",
                    "serve",
                    "--root",
                    str(root),
                    "--web-root",
                    str(args.web_root.resolve()),
                    "--port",
                    str(args.port),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        current = {"pid": child.pid, "instance": instance, "port": args.port}
        atomic_private(registration, json.dumps(current).encode())
        for _ in range(150):
            ready = probe()
            if ready:
                break
            if child.poll() is not None:
                raise SystemExit("Core 启动失败，请检查运行目录的 service.log")
            time.sleep(0.1)
        if not ready:
            child.terminate()
            child.wait(timeout=10)
            raise SystemExit("Core 就绪检查超时，已停止本次进程")
    if ready.get("instance") != current.get("instance"):
        raise SystemExit("此端口正在运行其他 Core 实例，请检查配置")
    if not args.no_open or args.output:
        from .access import Access
        access = Access(root / "access")
        ticket = access.ticket()
        url = origin + "/#ticket=" + ticket
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_private(args.output, json.dumps({"url": url}).encode())
        if not args.no_open:
            webbrowser.open(url)
    if args.command == "start-with-dsh":
        if sys.platform != "darwin":
            print("Core 已启动；请在本平台手动打开 DeepSeek Harness")
        else:
            subprocess.run(["open", "-a", "DeepSeek Harness"], check=True)
    print("未来道具研究所已就绪：" + origin)


if __name__ == "__main__":
    main()
