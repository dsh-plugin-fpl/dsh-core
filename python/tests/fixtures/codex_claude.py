import sys
from pathlib import Path


def fake_cli(root: Path, name="fake"):
    path = root / name
    path.write_text(
        f"#!{sys.executable}\n"
        + """
import sys, json, os, subprocess, re, base64, io

if "--version" in sys.argv:
    print("fake 2.163.300")
    raise SystemExit
if "--help" in sys.argv:
    print("--effort <level>   Effort level for the session")
    raise SystemExit
if sys.argv[-2:] in (["login", "status"], ["auth", "status"]):
    raise SystemExit
assert "FPL_SECRETS_KEY" not in os.environ
assert "FPL_STUDIO_TOKEN" not in os.environ


def send(x):
    print(json.dumps(x), flush=True)


if "app-server" in sys.argv:
    image_enabled = "features.image_generation=true" in sys.argv
    assert os.path.basename(os.environ["CODEX_HOME"]).startswith("codex-")
    assert "features.shell_tool=false" in sys.argv
    policy_flag = (
        'default_permissions="fpl-images"' if image_enabled else 'sandbox_mode="read-only"'
    )
    assert policy_flag in sys.argv
    for line in sys.stdin:
        x = json.loads(line)
        method = x.get("method")
        if method == "initialize":
            send({"id": x["id"], "result": {}})
        elif method == "config/read":
            send({"id": x["id"], "result": {"config": {
                "sandbox_mode": None if image_enabled else "read-only",
                "default_permissions": "fpl-images" if image_enabled else None,
                "permissions": {"fpl-images": {
                    "filesystem": {os.getcwd(): "read"}, "network": {"enabled": False}
                }} if image_enabled else {},
                "approval_policy": "never",
                "web_search": "disabled",
                "cli_auth_credentials_store": "file",
                "features": {
                    "image_generation": image_enabled,
                    "skip_host_skill_discovery": True,
                    "shell_tool": False,
                    "hooks": False,
                    "plugins": False,
                    "apps": False,
                    "multi_agent": False,
                    "skill_search": False,
                },
            }}})
        elif method == "account/read":
            assert x["params"]["refreshToken"] is False
            api_key_fixture = os.path.basename(sys.argv[0]) == "fixture-api-key"
            account_type = "apiKey" if api_key_fixture else "chatgpt"
            send({"id": x["id"], "result": {"account": {"type": account_type}}})
        elif method == "model/list":
            send(
                {
                    "id": x["id"],
                    "result": {"data": [{"model": "fake-model", "displayName": "Fake model",
                                        "serviceTiers": [{"id": "priority"}],
                                        "supportedReasoningEfforts": [
                                            {"reasoningEffort": "medium"},
                                            {"reasoningEffort": "high"},
                                        ]}]},
                }
            )
        elif method == "thread/start":
            assert x["params"]["ephemeral"]
            assert os.getcwd() == x["params"]["cwd"]
            assert os.path.basename(os.getcwd()).startswith("fpl-codex-work-")
            send({"id": x["id"], "result": {"thread": {"id": "fake-thread"}}})
        elif method == "turn/start":
            send({"id": x["id"], "result": {"turn": {"id": "fake-turn"}}})
            prompt = x["params"]["input"][0]["text"]
            try:
                history = json.loads(prompt.splitlines()[1])["messages"]
                marker = next(m["content"] for m in reversed(history) if m["role"] == "user")
            except (ValueError, KeyError, StopIteration, IndexError):
                marker = prompt
            if "GENERATE_FAKE" in marker:
                from PIL import Image
                assert image_enabled
                output = io.BytesIO()
                large = "LARGE_IMAGE" in marker
                Image.new("RGBA", (2200, 1500) if large else (5, 3), (10, 20, 30, 40)).save(
                    output, format="PNG", compress_level=0 if large else 6
                )
                params = {"threadId": "fake-thread", "turnId": "fake-turn", "item": {
                    "type": "imageGeneration", "id": "generated-item", "status": "in_progress"
                }}
                send({"method": "item/started", "params": params})
                params["item"].update(
                    status="completed", result=base64.b64encode(output.getvalue()).decode()
                )
                send({"method": "item/completed", "params": params})
                send({"method": "item/completed", "params": params})
                send({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
                break
            if "IMAGE_FAKE" in marker:
                from PIL import Image
                assert "kind=imports" in prompt and "read_file" in prompt
                images = [part["url"] for part in x["params"]["input"] if part["type"] == "image"]
                assert len(images) == 1
                assert images[0].startswith("data:image/jpeg;base64,")
                with Image.open(io.BytesIO(base64.b64decode(images[0].split(",", 1)[1]))) as image:
                    assert image.size == (1024, 512)
                    red, green, blue = image.getpixel((512, 256))
                    assert red > 200 and green < 50 and blue < 50
                send({"method": "item/agentMessage/delta", "params": {"delta": "已收到图片像素"}})
                send({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
                break
            if "EFFORT_FAKE" in marker:
                assert x["params"]["effort"] == "high"
                assert x["params"]["serviceTier"] == "fast"
                for total in (13, 20):
                    send({"method": "thread/tokenUsage/updated", "params": {
                        "threadId": "fake-thread",
                        "tokenUsage": {
                            "total": {
                                "inputTokens": total - 3, "outputTokens": 3, "totalTokens": total
                            },
                            "last": {"totalTokens": 11},
                            "modelContextWindow": 100,
                        },
                    }})
                send({"method": "item/completed", "params": {"item": {
                    "type": "reasoning", "id": "fixture-summary",
                    "summary": ["核对授权范围"], "content": ["raw-fixture-text"],
                }}})
            if "BIG_FAKE" in marker:
                send({"method": "item/agentMessage/delta", "params": {"delta": "x" * 100001}})
                send({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
                break
            if "SLOW_FAKE" in marker:
                import time
                send({"method": "item/agentMessage/delta", "params": {"delta": "正在运行"}})
                time.sleep(60)
            tool = (
                "ask_user"
                if "ASK_FAKE" in marker
                else "write_file"
                if "WRITE_FAKE" in marker
                else "operations"
            )
            args = (
                {"prompt": "Which one?", "options": ["A", "B"]}
                if tool == "ask_user"
                else {"name": "draft.txt", "content": "draft"}
                if tool == "write_file"
                else {}
            )
            draft = re.search(r"DRAFT_FAKE:([a-f0-9-]+):(OLD|NEW)", marker)
            if draft:
                tool = "call"
                project_id, version = draft.groups()
                args = {"operation": "visual.apply_canvas_operations", "arguments": {
                    "path_params": {"project_id": project_id},
                    "body": {"expected_revision": 0, "request_id": "draft-" + version,
                        "operations": [{"entity": "node", "action": "insert", "id": "video-draft",
                            "value": {"id": "video-draft", "kind": "video",
                                "title": "旧标题" if version == "OLD" else "新标题",
                                "position": {"x": 80, "y": 120},
                                "generation_draft": {"prompt": "日落城市镜头"}}}]}}}
            workspace_command = re.search(r"WORKSPACE_COMMAND:([A-Za-z0-9+/=]+)", marker)
            if workspace_command:
                tool = "execute_workspace"
                args = {"command": base64.b64decode(workspace_command[1]).decode()}
            send(
                {
                    "id": "tool-1",
                    "method": "item/tool/call",
                    "params": {"tool": tool, "arguments": args},
                }
            )
        elif x.get("id") == "tool-1":
            send({"method": "item/agentMessage/delta", "params": {"delta": "假 CLI 已核对项目"}})
            send({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
            break
else:
    assert "--restricted" in sys.argv and sys.argv[sys.argv.index("--tools") + 1] == ""
    if "--input-format" in sys.argv:
        prompt = json.loads(sys.stdin.readline())["message"]["content"]
        send({"type": "system", "subtype": "init"})
    else:
        prompt = sys.stdin.read()
    if "CLAUDE_EFFORT_FAKE" in prompt:
        assert sys.argv[sys.argv.index("--model") + 1] == "claude-sonnet-4-6"
        assert sys.argv[sys.argv.index("--effort") + 1] == "high"
    config = json.loads(sys.argv[sys.argv.index("--mcp-config") + 1])["mcpServers"]["fpl"]
    child = subprocess.Popen(
        [config["command"], *config["args"]],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, **config["env"]},
    )
    for identity, method, params in [
        (1, "initialize", {}),
        (2, "tools/list", {}),
        (3, "tools/call", {"name": "operations", "arguments": {}}),
    ]:
        child.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": identity, "method": method, "params": params})
            + "\\n"
        )
        child.stdin.flush()
        result = json.loads(child.stdout.readline())
        assert "result" in result and not result["result"].get("isError")
    child.stdin.close()
    child.wait(timeout=10)
    send(
        {
            "type": "stream_event",
            "event": {"delta": {"type": "text_delta", "text": "假 Claude 已核对项目"}},
        }
    )
    send({"type": "result", "subtype": "success", "is_error": False,
          "usage": {"input_tokens": 7, "output_tokens": 2, "cache_read_input_tokens": 3}})
"""
    )
    path.chmod(0o700)
    return str(path)
