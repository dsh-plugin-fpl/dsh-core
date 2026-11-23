"""Local model installation and bounded speech/video execution."""

import asyncio
import json
import shutil
from fractions import Fraction
from types import SimpleNamespace

from .contracts import CoreError
from .extracted.config.local_models import (
    FFMPEG_MODEL_ID,
    RELAY_MODEL_ID,
    ZONOS_MODEL_ID,
    compatible,
    definition,
    manifest,
    required_disk_bytes,
)
from .extracted.contracts.model_service.schemas.local_models import (
    SpeechSynthesisParameters,
    ZonosSynthesisParameters,
)
from .extracted.host.local_models.installer import Installer
from .extracted.host.local_models.relayvsr_runtime import RelayRuntime
from .extracted.host.local_models.runtime import LocalRuntime


async def process(*args, cwd=None, timeout=600):
    child = await asyncio.create_subprocess_exec(
        *map(str, args),
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        async with asyncio.timeout(timeout):
            out, _error = await child.communicate()
        if child.returncode:
            raise CoreError("本地媒体处理失败，请检查输入格式", "media_execution")
        return out
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()


class LocalModels:
    def __init__(self, store, resources):
        self.store, self.resources = store, resources
        self.installer = Installer(store.root / "local-models")
        self.runtime = LocalRuntime(self.installer)
        self.lock = asyncio.Lock()

    async def status(self):
        saved = self.store.config("local", {"enabled": []})
        rows = []
        for model in manifest()["models"]:
            ffmpeg = model["id"] == FFMPEG_MODEL_ID
            installed = (
                bool(shutil.which("ffmpeg"))
                if ffmpeg
                else await asyncio.to_thread(self.installer.is_installed, model)
            )
            rows.append(
                {
                    **{
                        k: model.get(k)
                        for k in (
                            "id",
                            "label",
                            "repository",
                            "license",
                            "model_revision",
                            "capability",
                        )
                    },
                    "download_bytes": sum(i["size"] for i in model["files"]),
                    "required_disk_bytes": required_disk_bytes(model),
                    "compatible": True if ffmpeg else compatible(),
                    "installed": installed,
                    "enabled": model["id"] in saved["value"]["enabled"],
                    "loaded": self.runtime.model_id == model["id"],
                    "busy": self.runtime.busy,
                }
            )
        return {"revision": saved["revision"], "items": rows}

    async def manage(self, model_id, action, emit):
        model = definition(model_id)
        if model_id != FFMPEG_MODEL_ID and not compatible():
            raise CoreError(
                "此模型需要 Apple Silicon 和 macOS 15 或更新版本", "unsupported_platform"
            )
        async with self.lock, self.runtime.serial:

            async def report(percent):
                await emit("progress", {"progress": int(percent), "model_id": model_id})

            if action in {"install", "repair"}:
                await self.runtime.close()
                if model_id == FFMPEG_MODEL_ID:
                    if not shutil.which("ffmpeg"):
                        raise CoreError(
                            "请先安装 FFmpeg：brew install ffmpeg", "dependency_missing"
                        )
                else:
                    await self.installer.install(model, report, repair=action == "repair")
            elif action in {"stop", "uninstall"}:
                if self.runtime.model_id == model_id:
                    await self.runtime.close()
                if action == "uninstall":
                    path = self.installer.model_path(model_id)
                    if path.exists():
                        await asyncio.to_thread(shutil.rmtree, path)
            elif action != "enable":
                raise CoreError("未知本地模型操作")
            current = self.store.config("local", {"enabled": []})
            enabled = set(current["value"]["enabled"])
            if action in {"install", "repair", "enable"}:
                if model_id != FFMPEG_MODEL_ID and not await asyncio.to_thread(
                    self.installer.is_installed, model
                ):
                    raise CoreError("请先安装该模型", "model_not_installed")
                enabled.add(model_id)
            else:
                enabled.discard(model_id)
            self.store.save_config("local", {"enabled": sorted(enabled)}, current["revision"])
        return await self.status()

    async def execute(self, request, emit, scratch):
        model = definition(request.model)
        enabled = self.store.config("local", {"enabled": []})["value"]["enabled"]
        if model["id"] not in enabled:
            raise CoreError("请在本地模型页启用该模型", "model_disabled")
        if request.capability == "speech_synthesis":
            if not request.resource_ids:
                raise CoreError("请选择参考音频")
            source, info = self.resources.path(request.resource_ids[0])
            values = {
                "text": request.prompt,
                "reference_asset_id": info["id"],
                "language": "Chinese",
                **request.parameters,
            }
            schema = (
                ZonosSynthesisParameters
                if request.model == ZONOS_MODEL_ID
                else SpeechSynthesisParameters
            )
            values = schema.model_validate(values).model_dump()
            values.pop("reference_asset_id")
            output = scratch / "speech.wav"
            values.update(reference_path=str(source), output_path=str(output))

            async def validate():
                if not await asyncio.to_thread(self.installer.is_installed, model):
                    raise CoreError("本地模型文件需要重新校验", "model_unavailable")

            metrics = await self.runtime.synthesize(request.model, values, validate)
            artifact = self.resources.put(
                output.read_bytes(), "speech.wav", "audio/wav", request.application_id
            )
            return {"resources": [artifact], "metrics": metrics}
        if request.capability != "video_enhancement" or not request.resource_ids:
            raise CoreError("请选择待增强视频")
        source, info = self.resources.path(request.resource_ids[0])
        output = scratch / "enhanced.mp4"
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise CoreError("请先安装 FFmpeg", "dependency_missing")
        if request.model == FFMPEG_MODEL_ID:
            await process(
                ffmpeg,
                "-nostdin",
                "-y",
                "-i",
                source,
                "-vf",
                "unsharp=5:5:0.8:3:3:0.4",
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-c:a",
                "aac",
                output,
            )
        else:
            probe = shutil.which("ffprobe")
            if not probe:
                raise CoreError("请安装 ffprobe", "dependency_missing")
            details = json.loads(
                await process(
                    probe,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height,r_frame_rate,nb_frames,duration",
                    "-of",
                    "json",
                    source,
                )
            )
            stream = details["streams"][0]
            video = SimpleNamespace(
                width=stream["width"], height=stream["height"], fps=stream["r_frame_rate"]
            )
            estimated = int(
                stream.get("nb_frames")
                or float(stream.get("duration") or 0) * float(Fraction(video.fps))
            )
            if not 0 < estimated <= 300 or video.width * video.height > 1920 * 1080:
                raise CoreError("RelayVSR 单次支持最多 300 帧、1080p 输入", "media_limit")
            raw = scratch / "input.rgb"
            await process(
                ffmpeg,
                "-nostdin",
                "-y",
                "-i",
                source,
                "-frames:v",
                "301",
                "-an",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                raw,
            )
            frames = raw.stat().st_size // (video.width * video.height * 3)
            if frames > 300 or video.width * video.height > 1920 * 1080:
                raise CoreError("RelayVSR 单次支持最多 300 帧、1080p 输入", "media_limit")
            relay = RelayRuntime(
                self.installer.relay_executable, self.installer.model_path(RELAY_MODEL_ID), scratch
            )
            try:
                await relay.enhance(video, frames)
            finally:
                await relay.close()
            await process(
                ffmpeg,
                "-nostdin",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                f"{video.width * 2}x{video.height * 2}",
                "-r",
                video.fps,
                "-i",
                scratch / "output.rgb",
                "-i",
                source,
                "-map",
                "0:v",
                "-map",
                "1:a?",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-shortest",
                output,
            )
        return {
            "resources": [
                self.resources.put(
                    output.read_bytes(), output.name, "video/mp4", request.application_id
                )
            ]
        }

    async def close(self):
        await self.runtime.close()
