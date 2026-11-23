from types import SimpleNamespace
import pytest
from dsh_core_worker.contracts import CoreError
from dsh_core_worker.model_options import application_options, transcription_parameters


def catalog(kind="senseaudio_asr_pro", enabled=True):
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
    assert options["transcription"]["items"][0]["max_input_bytes"] == 100 * 1024**2
    result = transcription_parameters(
        catalog(),
        {
            "model_id": "speech",
            "byte_size": 12 * 1024**2,
            "parameters": {"language": "zh-CN", "target_language": "en"},
        },
    )
    assert result["parameters"]["language"] == "Chinese"
    assert result["parameters"]["target_language"] == "English"
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
