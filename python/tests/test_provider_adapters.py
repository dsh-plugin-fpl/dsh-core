from io import BytesIO

import httpx
import pytest
from dsh_core_worker.extracted.apps.model_service.providers.streaming import (
    ChatMessageInput,
    ImageInput,
    stream_text_completion,
)
from dsh_core_worker.extracted.apps.model_service.providers.text import invoke_diagnostic
from dsh_core_worker.extracted.apps.model_service.providers.transcription import (
    transcribe_model_audio,
)


@pytest.mark.asyncio
async def test_openai_compatible_transcription_adapter_returns_plain_text() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://gateway.example/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer openai-secret"
        body = (await request.aread()).decode()
        assert 'name="model"' in body
        assert "whisper-compatible" in body
        assert 'name="language"' in body
        assert "ja" in body
        assert 'filename="voice.ogg"' in body
        return httpx.Response(200, json={"text": "こんにちは。"})

    result = await transcribe_model_audio(
        provider_kind="openai",
        base_url="https://gateway.example/v1/",
        api_key="openai-secret",
        model="whisper-compatible",
        filename="voice.ogg",
        source=BytesIO(b"OggSvoice"),
        mime_type="audio/ogg",
        byte_size=9,
        language="ja",
        transport=httpx.MockTransport(handler),
    )

    assert result.text == "こんにちは。"
    assert result.segments == ()


@pytest.mark.asyncio
async def test_openai_compatible_diagnostic_normalizes_usage() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret"
        assert b"secret" not in request.content
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "OK"}}],
                "usage": {"prompt_tokens": 6, "completion_tokens": 1, "total_tokens": 7},
            },
        )

    result = await invoke_diagnostic(
        kind="openai",
        base_url="https://gateway.example/v1",
        api_key="secret",
        model="example-model",
        transport=httpx.MockTransport(handler),
    )
    assert result.output == "OK"
    assert result.total_tokens == 7


@pytest.mark.asyncio
async def test_anthropic_diagnostic_normalizes_content_and_usage() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "secret"
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "OK"}],
                "usage": {"input_tokens": 5, "output_tokens": 1},
            },
        )

    result = await invoke_diagnostic(
        kind="anthropic",
        base_url="https://api.anthropic.com/v1",
        api_key="secret",
        model="claude-example",
        transport=httpx.MockTransport(handler),
    )
    assert result.output == "OK"
    assert result.prompt_tokens == 5
    assert result.completion_tokens == 1
    assert result.total_tokens == 6


@pytest.mark.asyncio
async def test_openai_streaming_normalizes_deltas_and_usage() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = request.read().decode()
        assert '"stream":true' in body
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"A"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"B"}}]}\n\n'
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                'data: {"choices":[],"usage":{"prompt_tokens":3,'
                '"completion_tokens":2,"total_tokens":5}}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    chunks = [
        chunk
        async for chunk in stream_text_completion(
            kind="openai",
            base_url="https://gateway.example/v1",
            api_key="secret",
            model="model",
            system_prompt="system",
            messages=[ChatMessageInput(role="user", text="hello")],
            temperature=0.7,
            max_tokens=100,
            transport=httpx.MockTransport(handler),
        )
    ]
    assert "".join(chunk.text for chunk in chunks) == "AB"
    assert chunks[-1].total_tokens == 5
    assert any(chunk.finish_reason == "stop" for chunk in chunks)


@pytest.mark.asyncio
async def test_anthropic_streaming_normalizes_deltas_and_usage() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"type":"message_start","message":{"usage":{"input_tokens":4}}}\n\n'
                'data: {"type":"content_block_delta","delta":{"type":"text_delta",'
                '"text":"Hi"}}\n\n'
                'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
                '"usage":{"output_tokens":2}}\n\n'
                'data: {"type":"message_stop"}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    chunks = [
        chunk
        async for chunk in stream_text_completion(
            kind="anthropic",
            base_url="https://api.anthropic.com/v1",
            api_key="secret",
            model="claude",
            system_prompt="",
            messages=[ChatMessageInput(role="user", text="hello")],
            temperature=0.2,
            max_tokens=50,
            transport=httpx.MockTransport(handler),
        )
    ]
    assert "".join(chunk.text for chunk in chunks) == "Hi"
    assert chunks[-1].prompt_tokens == 4
    assert chunks[-1].completion_tokens == 2
    assert chunks[-1].total_tokens == 6
    assert chunks[-1].finish_reason == "end_turn"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["openai", "anthropic"])
async def test_streaming_maps_image_inputs_without_losing_order(kind: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        assert body.index("base64-image-one") < body.index("base64-image-two")
        assert body.index("base64-image-two") < body.index("describe both")
        if kind == "openai":
            assert '"type":"image_url"' in body
            assert '"url":"data:image/png;base64,base64-image-one"' in body
            response = 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
        else:
            assert '"type":"image"' in body
            assert '"media_type":"image/png"' in body
            assert '"data":"base64-image-one"' in body
            response = (
                'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ok"}}\n\n'
                'data: {"type":"message_stop"}\n\n'
            )
        return httpx.Response(200, text=response, headers={"content-type": "text/event-stream"})

    chunks = [
        chunk
        async for chunk in stream_text_completion(
            kind=kind,
            base_url="https://gateway.example/v1",
            api_key="secret",
            model="vision-model",
            system_prompt="",
            messages=[
                ChatMessageInput(
                    role="user",
                    text="describe both",
                    images=(
                        ImageInput("image/png", "base64-image-one", 10),
                        ImageInput("image/webp", "base64-image-two", 20),
                    ),
                )
            ],
            temperature=0.2,
            max_tokens=50,
            transport=httpx.MockTransport(handler),
        )
    ]
    assert "".join(chunk.text for chunk in chunks) == "ok"
