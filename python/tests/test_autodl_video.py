import json

import httpx
import pytest
from dsh_core_worker.extracted.apps.model_service.providers.autodl_video import (
    create_task,
    get_task,
)
from dsh_core_worker.extracted.apps.model_service.providers.autodl_workflows import (
    MOTION,
    PREFIX,
    TITI,
    TITIM,
    WORKFLOWS,
    capabilities,
    normalize_request,
    prepare_payload,
    reference_upload_url,
    require_reference_delivery,
    validate_references,
)


def request(model):
    spec = WORKFLOWS[model]
    payload = {
        "prompt": "subject walks" if spec.prompt and spec.prompt_required else "",
        "mode": spec.mode,
        "duration": 5,
        "reference_asset_ids": [f"image-{i}" for i in range(spec.images_min)],
        "audio_asset_ids": [f"audio-{i}" for i in range(spec.audio_min)],
        "video_asset_ids": ["video-0"] if spec.video_required else [],
    }
    if spec.mode == "frames":
        payload.update(
            reference_asset_ids=[], first_frame_asset_id="first", last_frame_asset_id="last"
        )
    return payload


def content(payload):
    result = []
    for key in ("first_frame", "last_frame"):
        if payload.get(key + "_asset_id"):
            result.append({"type": "image", "role": key, "url": key})
    for kind, field in (
        ("image", "reference_asset_ids"),
        ("audio", "audio_asset_ids"),
        ("video", "video_asset_ids"),
    ):
        result.extend(
            {"type": kind, "url" if kind == "image" else f"{kind}_url": identity}
            for identity in payload[field]
        )
    return result


@pytest.mark.parametrize("model", list(WORKFLOWS))
@pytest.mark.asyncio
async def test_each_workflow_submits_its_exact_pinned_contract(model):
    params = request(model)
    if model == TITIM:
        params["reference_asset_ids"] = ["image-b", "image-a"]
        params["workflow_parameters"] = {"video_subject_to_replace": " white dog, person "}
    payload = normalize_request(model, params)
    before = json.dumps(payload)
    assets = content(payload)
    if model in {TITI, TITIM}:
        for asset in assets:
            key = "url" if asset["type"] == "image" else "video_url"
            asset[key] = f"https://upload.example/{asset[key]}"
    wire = prepare_payload(model, payload, assets)
    seen = []

    def handle(req):
        seen.append(req)
        assert req.headers["Authorization"] == "raw-token"
        assert req.url.path.endswith("/" + model)
        assert json.loads(req.content) == wire
        return httpx.Response(200, json={"code": "Success", "data": {"task_id": "upstream"}})

    assert (
        await create_task(
            base_url="",
            api_key="raw-token",
            model=model,
            payload=payload,
            content=assets,
            transport=httpx.MockTransport(handle),
        )
        == "upstream"
    )
    assert len(seen) == 1 and json.dumps(payload) == before
    assert wire["resolution"] == ("464*832px(竖版)" if model == MOTION else "768p竖")
    assert ("seed" in wire) == (WORKFLOWS[model].seed_min is not None)
    assert ("prompt" in wire) == bool(params["prompt"])
    if model == MOTION:
        assert set(wire) == {"resolution", "seed", "ref_image", "ref_video"}
    elif model in {TITI, TITIM}:
        expected = {
            "resolution": "768p竖",
            "duration": 5,
            "ref_video": "https://upload.example/video-0",
        }
        if model == TITI:
            expected["ref_image"] = "https://upload.example/image-0"
        else:
            expected.update(
                ref_image_0="https://upload.example/image-b",
                ref_image_1="https://upload.example/image-a",
                video_subject_to_replace="white dog, person",
            )
        assert wire == expected
    elif model == PREFIX + "image_audio_to_video":
        assert set(wire) == {"resolution", "audio_duration", "ref_image_0", "ref_audio_0"}
    elif model == PREFIX + "lightx2v":
        assert wire["first_frame"] == "first_frame" and wire["last_frame"] == "last_frame"
        assert "ref_image_0" not in wire


@pytest.mark.parametrize("model", list(WORKFLOWS))
def test_boundaries_defaults_seed_snapshot_and_mime(model):
    payload = request(model)
    normalized = normalize_request(model, payload)
    assert normalize_request(model, normalized) == normalized
    if model != MOTION:
        for duration in (0, WORKFLOWS[model].duration_max + 1, -1):
            with pytest.raises(ValueError, match="时长"):
                normalize_request(model, {**payload, "duration": duration})
    if WORKFLOWS[model].prompt:
        maximum = WORKFLOWS[model].max_prompt_length
        normalize_request(model, {**payload, "prompt": "a" * maximum})
        with pytest.raises(ValueError, match="提示词"):
            normalize_request(model, {**payload, "prompt": "a" * (maximum + 1)})
    if WORKFLOWS[model].seed_min is not None:
        for seed in (WORKFLOWS[model].seed_min - 1, 10**15, 1.5, True):
            with pytest.raises(ValueError, match="种子"):
                normalize_request(model, {**payload, "workflow_parameters": {"seed": seed}})
    else:
        with pytest.raises(ValueError, match="参数"):
            normalize_request(model, {**payload, "workflow_parameters": {"seed": 1}})
    with pytest.raises(ValueError, match="数量|需要"):
        normalize_request(model, {**payload, "audio_asset_ids": [str(i) for i in range(4)]})
    with pytest.raises(ValueError, match="格式"):
        validate_references(model, [("image", "image/gif", 1)])
    with pytest.raises(ValueError, match="45 MB"):
        validate_references(model, [("image", "image/png", 46 * 1024 * 1024)])


@pytest.mark.parametrize("model", [TITI, TITIM])
def test_character_replacement_requires_the_right_assets_and_https_delivery(model):
    params = request(model)
    assert capabilities(model).reference_video_upload_configured is False
    assert reference_upload_url("autodl_video", model, "default", "dedicated") == "dedicated"
    assert reference_upload_url("autodl_video", model, "default", "") == "default"
    assert reference_upload_url("volcengine_video", "seedance", "default", "dedicated") == "default"
    with pytest.raises(ValueError, match="临时上传服务"):
        require_reference_delivery(model, upload_configured=False)
    with pytest.raises(ValueError, match="时长"):
        normalize_request(model, {**params, "duration": 4})
    for field in ("reference_asset_ids", "video_asset_ids"):
        with pytest.raises(ValueError, match="需要"):
            normalize_request(model, {**params, field: []})
    with pytest.raises(ValueError, match="需要"):
        normalize_request(
            model,
            {
                **params,
                "reference_asset_ids": [
                    str(i) for i in range(capabilities(model).reference_limits["image"] + 1)
                ],
            },
        )
    with pytest.raises(ValueError, match="格式"):
        validate_references(model, [("video", "video/quicktime", 1)])
    with pytest.raises(ValueError, match="HTTPS"):
        prepare_payload(model, normalize_request(model, params), content(params))
    expected = "英文" if model == TITIM else "不支持"
    with pytest.raises(ValueError, match=expected):
        normalize_request(
            model, {**params, "workflow_parameters": {"video_subject_to_replace": "人物"}}
        )


def test_resolution_pairs_order_and_audio_only_capabilities():
    for suffix in ("zm_nono", "zm_u30"):
        with pytest.raises(ValueError, match="尺寸"):
            normalize_request(
                PREFIX + suffix, {**request(PREFIX + suffix), "resolution": "960p", "ratio": "16:9"}
            )
    model = PREFIX + "zm_u24"
    payload = {
        **request(model),
        "reference_asset_ids": ["B", "A"],
        "audio_asset_ids": ["Y", "X"],
        "ratio": "1:1",
        "resolution": "480p",
    }
    wire = prepare_payload(model, normalize_request(model, payload), content(payload))
    assert wire["resolution"] == "480p(1:1)"
    assert [wire["ref_image_0"], wire["ref_image_1"], wire["ref_audio_0"], wire["ref_audio_1"]] == [
        "B",
        "A",
        "Y",
        "X",
    ]
    for suffix in ("image_audio_to_video_v2", "image_audio_to_video_v2_15s"):
        model = PREFIX + suffix
        assert capabilities(model).audio_only
        normalize_request(model, {**request(model), "audio_asset_ids": ["voice"]})
        normalize_request(model, {**request(model), "mode": "text"})
        validate_references(model, [("audio", "audio/flac", 10)])
    with pytest.raises(ValueError):
        validate_references(PREFIX + "zm_nono", [("audio", "audio/flac", 10)])
    validate_references(MOTION, [("video", "video/webm", 10)])


@pytest.mark.parametrize(
    "remote,expected",
    [
        ("QUEUED", "pending"),
        ("RUNNING", "processing"),
        ("SUCCESS", "completed"),
        ("completed", "completed"),
        ("FAILED", "failed"),
        ("cancelled", "failed"),
    ],
)
@pytest.mark.asyncio
async def test_status_normalization(remote, expected):
    data = {"status": remote, "results": [{"type": "video", "url": "https://cdn.example/out.mp4"}]}
    result = await get_task(
        base_url="",
        api_key="raw-token",
        task_id="remote",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"code": "Success", "data": data})
        ),
    )
    assert result.status == expected


@pytest.mark.asyncio
async def test_business_http_errors_do_not_retry_and_redact_raw_authorization():
    sent = []

    def fail(req):
        sent.append(req)
        return httpx.Response(429, json={"code": "Failure", "msg": "raw-secret rejected"})

    model = PREFIX + "lightx2v_no_pic"
    with pytest.raises(ValueError, match="REDACTED") as error:
        await create_task(
            base_url="",
            api_key="raw-secret",
            model=model,
            payload=normalize_request(model, request(model)),
            content=[],
            transport=httpx.MockTransport(fail),
        )
    assert "raw-secret" not in str(error.value) and len(sent) == 1
    with pytest.raises(ValueError, match="视频地址"):
        await get_task(
            base_url="",
            api_key="test",
            task_id="task",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, json={"code": "Success", "data": {"status": "SUCCESS", "results": []}}
                )
            ),
        )
