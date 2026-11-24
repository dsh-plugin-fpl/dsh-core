import json
import wave

import httpx
import pytest
from dsh_core_worker.extracted.apps.model_service.providers.seedance_parameters import (
    normalize_video_parameters,
    video_capabilities,
)
from dsh_core_worker.extracted.apps.model_service.providers.seedance_references import (
    SeedanceReference,
    validate_seedance_references,
)
from dsh_core_worker.extracted.apps.model_service.providers.volcengine_video import (
    create_task,
    get_task,
)
from PIL import Image

V20 = "doubao-seedance-2-0-260128"
V25 = "doubao-seedance-2-5-260628"


@pytest.mark.asyncio
@pytest.mark.parametrize("slug,duration", [(V20, 15), (V25, 30)])
async def test_native_creation_converts_roles_preserves_order_and_uses_official_defaults(
    slug, duration
):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer fake"
        return httpx.Response(200, json={"id": "remote-1"})

    content = [
        {"type": "text", "text": "实际提示词"},
        {"type": "image", "url": "data:image/png;base64,AA==", "role": "reference"},
        {"type": "audio", "audio_url": "data:audio/wav;base64,AA=="},
        {"type": "video", "video_url": "https://media.example/ref.mp4"},
    ]
    original = json.dumps(content)
    result = await create_task(
        base_url="",
        api_key="fake",
        model=slug,
        payload={"duration": duration, "mode": "reference"},
        content=content,
        transport=httpx.MockTransport(handle),
    )
    assert result == "remote-1" and json.dumps(content) == original
    assert (
        str(requests[0].url)
        == "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
    )
    body = json.loads(requests[0].content)
    assert body["generate_audio"] is True and body["watermark"] is True
    assert body["content"][1] == {
        "type": "image_url",
        "image_url": {"url": content[1]["url"]},
        "role": "reference_image",
    }
    assert body["content"][2]["role"] == "reference_audio"
    assert body["content"][3]["video_url"]["url"].endswith("ref.mp4")
    assert "provider_specific" not in body and "seed" not in body
    if slug == V25:
        assert body["output_format"] == "mp4" and body["omni_reference_task_type"] == "reference"


@pytest.mark.asyncio
async def test_first_last_frame_wire_and_no_implicit_post_retry():
    sent = []

    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(429, json={"error": {"message": "Bearer secret api_key=secret"}})

    with pytest.raises(ValueError, match="REDACTED"):
        await create_task(
            base_url="https://ark.example/api/v3",
            api_key="fake",
            model=V25,
            payload={"duration": -1, "mode": "frames", "ratio": "adaptive"},
            content=[
                {"type": "image", "url": "first", "role": "first_frame"},
                {"type": "image", "url": "last", "role": "last_frame"},
            ],
            transport=httpx.MockTransport(handle),
        )
    assert len(sent) == 1
    assert [r["role"] for r in sent[0]["content"]] == ["first_frame", "last_frame"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "remote,local",
    [
        ("queued", "pending"),
        ("running", "processing"),
        ("succeeded", "completed"),
        ("failed", "failed"),
        ("cancelled", "failed"),
        ("expired", "failed"),
    ],
)
async def test_remote_status_is_normalized(remote, local):
    def handle(request):
        assert str(request.url).endswith("/contents/generations/tasks/remote-1")
        return httpx.Response(
            200, json={"status": remote, "content": {"video_url": "https://cdn.example/out.mp4"}}
        )

    state = await get_task(
        base_url="https://ark.example/api/v3",
        api_key="fake",
        task_id="remote-1",
        transport=httpx.MockTransport(handle),
    )
    assert state.status == local
    assert state.progress == (100 if remote == "succeeded" else 0)
    if remote in {"expired", "cancelled"}:
        assert state.error_message


@pytest.mark.parametrize(
    "slug,params",
    [
        (V20, {"duration": 30}),
        (V25, {"duration": 0}),
        (V25, {"duration": 31}),
        (V25, {"resolution": "4k"}),
        (V20, {"reference_asset_ids": list(range(10))}),
        (V25, {"reference_asset_ids": list(range(31))}),
        (V20, {"audio_asset_ids": ["a"]}),
        (V25, {"mode": "frames", "ratio": "16:9"}),
    ],
)
def test_version_policy_rejects_invalid_combinations(slug, params):
    with pytest.raises(ValueError):
        normalize_video_parameters("volcengine_video", slug, params)


def test_version_capabilities_and_legacy_boundaries():
    assert video_capabilities("volcengine_video", V25).reference_limits == {
        "image": 30,
        "video": 10,
        "audio": 10,
    }
    assert (
        normalize_video_parameters(
            "volcengine_video", V25, {"audio_asset_ids": ["a"], "duration": -1}
        )["duration"]
        == -1
    )
    assert (
        normalize_video_parameters("volcengine_video", V20, {"resolution": "4k"})["resolution"]
        == "4k"
    )
    for params in ({"duration": 30}, {"ratio": "adaptive"}, {"audio_asset_ids": ["a"]}):
        with pytest.raises(ValueError):
            normalize_video_parameters("zealman_video", V20, params)
    from dsh_core_worker.contracts import Connection

    provider = Connection(id="ark", name="Ark", kind="volcengine_video")
    assert provider.base_url == "https://ark.cn-beijing.volces.com/api/v3"


@pytest.mark.asyncio
async def test_reference_preflight_checks_actual_files_and_config_before_submission(tmp_path):
    picture = tmp_path / "image.png"
    Image.new("RGB", (512, 512)).save(picture)
    audio = tmp_path / "voice.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 8000 * 2)
    refs = [
        SeedanceReference("image", "image/png", picture),
        SeedanceReference("audio", "audio/wav", audio),
    ]
    await validate_seedance_references(V25, {}, refs, "")
    with pytest.raises(ValueError, match="temp_media_upload_url"):
        await validate_seedance_references(
            V25, {}, [SeedanceReference("video", "video/mp4", tmp_path / "missing")], ""
        )
    Image.new("RGB", (64, 64)).save(picture)
    with pytest.raises(ValueError, match="300"):
        await validate_seedance_references(V20, {}, refs[:1], "")
    with pytest.raises(ValueError, match="累计时长"):
        await validate_seedance_references(V20, {}, [refs[1]] * 8, "")
