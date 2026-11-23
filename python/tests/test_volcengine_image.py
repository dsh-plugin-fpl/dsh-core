import json

import httpx
import pytest
from dsh_core_worker.extracted.apps.model_service.providers.volcengine_image import (
    SEEDREAM_MODEL,
    generate_image,
)


@pytest.mark.asyncio
async def test_native_image_array_order_single_output_and_errors():
    submitted = []

    async def handler(request):
        assert str(request.url) == "https://ark.example/api/v3/images/generations"
        submitted.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"url": "https://cdn.example/output.png"}]})

    references = [f"data:image/png;base64,reference-{index}" for index in range(14)]
    result = await generate_image(
        base_url="https://ark.example/api/v3",
        api_key="fake-key",
        model=SEEDREAM_MODEL,
        payload={"prompt": "小夏和清晨场景", "size": "2048x2048"},
        references=references,
        transport=httpx.MockTransport(handler),
    )
    assert result == "https://cdn.example/output.png"
    assert submitted[0]["image"] == references
    assert submitted[0]["sequential_image_generation"] == "disabled"
    assert submitted[0]["response_format"] == "url"
    with pytest.raises(ValueError, match="14"):
        await generate_image(
            base_url="https://ark.example/api/v3",
            api_key="fake-key",
            model=SEEDREAM_MODEL,
            payload={"prompt": "test", "size": "2048x2048"},
            references=[*references, "extra"],
            transport=httpx.MockTransport(handler),
        )
    assert len(submitted) == 1
