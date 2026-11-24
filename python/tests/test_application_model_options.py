from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from dsh_core_worker.contracts import CoreError
from dsh_core_worker.model_options import application_options, transcription_parameters
from dsh_core_worker.service import create_app


def catalog(kind="openai", enabled=True):
    return SimpleNamespace(
        view=lambda: {
            "providers": [
                {
                    "id": "provider",
                    "name": "ASR",
                    "kind": kind,
                    "enabled": enabled,
                    "models": [
                        {
                            "id": "speech",
                            "slug": "asr-pro",
                            "label": "识别",
                            "description": "",
                            "enabled": True,
                            "capabilities": ["speech_recognition"],
                            "categories": [],
                        }
                    ],
                }
            ]
        }
    )


def test_asr_options_preserve_source_language_defaults_and_limits():
    options = application_options(catalog())
    assert options["transcription"]["items"][0]["max_input_bytes"] == 10 * 1024**2
    assert options["transcription"]["max_input_bytes"] == 10 * 1024**2
    result = transcription_parameters(
        catalog(),
        {
            "model_id": "speech",
            "byte_size": 1024,
            "parameters": {"language": "zh-CN"},
        },
    )
    assert result["parameters"]["language"] == "zh"
    assert result["parameters"]["target_language"] is None
    with pytest.raises(CoreError) as error:
        transcription_parameters(
            catalog("openai"), {"model_id": "speech", "byte_size": 12 * 1024**2, "parameters": {}}
        )
    assert error.value.code == "asr_input_too_large"
    with pytest.raises(CoreError) as error:
        transcription_parameters(
            catalog("openai"),
            {"model_id": "speech", "byte_size": 20, "parameters": {"target_language": "en"}},
        )
    assert error.value.code == "target_language_unsupported"
    with pytest.raises(CoreError):
        transcription_parameters(
            catalog(enabled=False), {"model_id": "speech", "byte_size": 20, "parameters": {}}
        )


def test_browser_transcription_options_use_the_remaining_provider_limit(tmp_path):
    origin = "http://testserver"
    app = create_app(tmp_path / "core", origin=origin)
    with TestClient(app) as client:
        login = client.post("/v1/session", json={"ticket": app.state.access.ticket()},
                            headers={"Origin": origin}).json()
        client.headers.update({"Origin": origin, "X-Core-CSRF": login["csrf"]})
        created = client.post("/v1/model-providers", json={
            "name": "Whisper", "kind": "openai", "base_url": "https://example.com/v1",
            "expected_catalog_revision": 0,
        })
        assert created.status_code == 200
        identity = created.json()["provider"]["id"]
        model = client.post(f"/v1/model-providers/{identity}/models", json={
            "slug": "whisper", "capabilities": ["speech_recognition"], "expected_catalog_revision": 1,
        })
        assert model.status_code == 200
        options = client.get("/v1/model-test/transcription-options").json()
        assert options["max_input_bytes"] == 10 * 1024**2
        assert options["items"][0]["max_input_bytes"] == options["max_input_bytes"]
        assert options["items"][0]["provider_kind"] == "openai"
