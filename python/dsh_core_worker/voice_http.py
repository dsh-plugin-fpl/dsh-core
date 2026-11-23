"""Same-origin browser voice transport reusing the existing bounded provider bridge."""

import asyncio

from fastapi import WebSocket, WebSocketDisconnect

from .contracts import CoreError
from .extracted.apps.model_service.providers.realtime import constants as c
from .extracted.apps.model_service.providers.realtime.contracts import ResolvedRealtimeModel
from .extracted.apps.model_service.providers.realtime.senseaudio import SenseAudioRealtimeBridge


def register_voice(app, origin):
    def number(default, low, high, step):
        return {"default": default, "minimum": low, "maximum": high, "step": step}

    @app.get("/v1/model-test/realtime-options")
    async def options():
        return {
            "models": [
                {
                    "id": m["id"],
                    "provider_id": p["id"],
                    "provider_name": p["name"],
                    "provider_kind": p["kind"],
                    "slug": m["slug"],
                    "label": m["label"] or m["slug"],
                    "voices": [
                        {"value": v, "label": label} for v, label in c.SENSEAUDIO_VOICE_PRESETS
                    ],
                    "default_voice": c.SENSEAUDIO_DEFAULT_VOICE,
                    "input_sample_rate": c.VOICE_INPUT_SAMPLE_RATE,
                    "output_sample_rate": c.VOICE_OUTPUT_SAMPLE_RATE,
                    "commit_silence_ms": c.VOICE_COMMIT_SILENCE_MS,
                    "supports_interruption": True,
                    "instructions_max_chars": c.VOICE_INSTRUCTIONS_MAX_CHARS,
                    "greeting_max_chars": c.VOICE_GREETING_MAX_CHARS,
                    "input_format": "pcm_s16le",
                    "output_format": "pcm_s16le",
                    "channels": 1,
                    "microphone": {
                        "echo_cancellation": True,
                        "noise_suppression": True,
                        "auto_gain_control": True,
                    },
                    "playback": {
                        "start_buffer_ms": number(400, 0, 2000, 50),
                        "recovery_buffer_ms": number(800, 0, 3000, 50),
                    },
                    "client_vad": {
                        "positive_speech_threshold": number(0.55, 0.05, 1, 0.01),
                        "negative_speech_threshold": number(0.38, 0, 0.95, 0.01),
                        "min_speech_ms": number(320, 64, 5000, 32),
                        "pre_speech_pad_ms": number(160, 0, 2000, 32),
                        "commit_silence_ms": number(400, 100, 5000, 50),
                    },
                }
                for p in app.state.catalog.view()["providers"]
                if p["enabled"] and p["kind"] == "senseaudio_realtime"
                for m in p["models"]
                if m["enabled"] and "voice" in m["capabilities"]
            ]
        }

    @app.websocket("/v1/model-test/realtime")
    async def realtime(socket: WebSocket):
        if (
            socket.headers.get("host") != origin.split("://")[1]
            or socket.headers.get("origin") != origin
            or not app.state.access.session(socket.cookies.get("core_session"))
        ):
            await socket.close(code=1008)
            return
        await socket.accept()
        bridge, sender = None, None
        try:
            async with asyncio.timeout(10):
                start = await socket.receive_json()
            if start.get("type") != "start":
                raise CoreError("请先选择实时语音模型")
            provider, model = app.state.catalog.find(start["model_id"])
            if (
                not provider["enabled"]
                or not model["enabled"]
                or provider["kind"] != "senseaudio_realtime"
                or "voice" not in model["capabilities"]
            ):
                raise CoreError("实时语音模型不可用")
            resolved = ResolvedRealtimeModel(
                id=model["id"],
                provider_id=provider["id"],
                provider_name=provider["name"],
                provider_kind=provider["kind"],
                slug=model["slug"],
                label=model["label"],
                base_url=provider["base_url"],
                api_key=app.state.credentials.secret(
                    "PROVIDER_" + provider["id"].replace("-", "_")
                ),
            )
            instructions, greeting = (
                str(start.get("instructions", "")),
                str(start.get("greeting", "")),
            )
            if (
                len(instructions) > c.VOICE_INSTRUCTIONS_MAX_CHARS
                or len(greeting) > c.VOICE_GREETING_MAX_CHARS
            ):
                raise CoreError("语音指令或开场白过长")
            bridge = SenseAudioRealtimeBridge(
                model=resolved,
                instructions=instructions,
                greeting=greeting,
                voice=start.get("voice", ""),
            )
            await bridge.start()

            async def transmit():
                while True:
                    await socket.send_json(await bridge.outbound.get())

            sender = asyncio.create_task(transmit())
            while True:
                message = await socket.receive_json()
                kind = message.get("type")
                if kind == "stop":
                    break
                if kind == "audio":
                    bridge.append_audio(message.get("data", ""))
                elif kind == "commit_input":
                    bridge.commit_input(message.get("turn_id"))
                elif kind == "interrupt":
                    bridge.interrupt(message.get("turn_id"))
        except WebSocketDisconnect:
            pass
        except Exception as error:  # noqa: BLE001 — sanitize transport/provider boundary
            try:
                await socket.send_json(
                    {
                        "type": "error",
                        "code": getattr(error, "code", "voice_unavailable"),
                        "message": str(error)
                        if isinstance(error, CoreError)
                        else "语音服务暂不可用",
                    }
                )
            except RuntimeError:
                pass
        finally:
            if sender:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
            if bridge:
                await bridge.close()
