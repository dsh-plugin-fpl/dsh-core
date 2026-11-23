import json

import httpx
import pytest
from dsh_core_worker.extracted.apps.model_service.providers.senseaudio_audio import (
    create_music_task,
    create_sound_effect,
    get_music_task,
)


@pytest.mark.asyncio
async def test_senseaudio_audio_provider_payloads_and_results_follow_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v2/music/song/create":
            return httpx.Response(200, json={"task_id": "music-task"})
        if request.url.path == "/v1/music/song/pending/music-task":
            return httpx.Response(
                200,
                json={
                    "status": "SUCCESS",
                    "response": {"data": [{"audio_url": "https://cdn.example/bgm.wav"}]},
                },
            )
        return httpx.Response(
            200,
            json={
                "generation_id": "sfx-generation",
                "status": "completed",
                "items": [{"audio_url": "https://cdn.example/door.mp3"}],
            },
        )

    transport = httpx.MockTransport(handler)
    task_id = await create_music_task(
        base_url="https://api.senseaudio.cn/v1",
        api_key="secret",
        model="senseaudio-music-2.0-260626",
        prompt="稳定、克制的片头音乐",
        theme="未来实验室",
        style="氛围电子乐",
        duration=30,
        output_format="wav",
        reference_urls=["https://cdn.example/reference.mp3"],
        transport=transport,
    )
    assert task_id == "music-task"
    music_body = json.loads(requests[0].content)
    assert music_body["mode"] == "instrumental"
    assert music_body["audio_settings"] == {"format": "wav", "duration": 30}
    assert music_body["attachments"] == [
        {"type": "audio", "url": "https://cdn.example/reference.mp3"}
    ]
    status = await get_music_task(
        base_url="https://api.senseaudio.cn/v1",
        api_key="secret",
        task_id=task_id,
        transport=transport,
    )
    assert status.status == "completed"
    assert status.result_url == "https://cdn.example/bgm.wav"

    sfx_url = await create_sound_effect(
        base_url="https://api.senseaudio.cn/v1",
        api_key="secret",
        model="senseaudio-sfx-1.0-260626",
        text="木门被敲响三次",
        duration=3,
        smart_duration=False,
        prompt_influence=0.7,
        output_format="mp3",
        transport=transport,
    )
    assert sfx_url == "https://cdn.example/door.mp3"
    sfx_body = json.loads(requests[-1].content)
    assert sfx_body["variants_count"] == 1
    assert sfx_body["duration_seconds"] == 3
    assert sfx_body["smart_duration"] is False
