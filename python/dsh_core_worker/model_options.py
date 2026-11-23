"""Redacted model options for application writing and speech recognition ports."""

from types import SimpleNamespace


def application_options(catalog):
    from .extracted.apps.model_service.transcription.parameters import parameter_capabilities

    rows = []
    transcription = []
    for provider in catalog.view()["providers"]:
        if not provider["enabled"]:
            continue
        for model in provider["models"]:
            if not model["enabled"]:
                continue
            rows.append(
                {
                    "byte_limit": 100 * 1024**2
                    if provider["kind"] == "senseaudio_asr_pro"
                    else 10 * 1024**2,
                    "id": model["id"],
                    "label": provider["name"] + " · " + model["label"],
                    "connection_id": provider["id"],
                    "provider_id": provider["id"],
                    "provider_name": provider["name"],
                    "model": model["slug"],
                    "capabilities": model["capabilities"],
                }
            )
            if "speech_recognition" in model["capabilities"]:
                transcription.append(
                    {
                        "id": model["id"],
                        "provider_id": provider["id"],
                        "provider_name": provider["name"],
                        "provider_kind": provider["kind"],
                        "slug": model["slug"],
                        "label": model["label"],
                        "description": model["description"],
                        "max_input_bytes": 100 * 1024**2
                        if provider["kind"] == "senseaudio_asr_pro"
                        else 10 * 1024**2,
                        "parameter_capabilities": parameter_capabilities(
                            SimpleNamespace(**model), SimpleNamespace(**provider)
                        ).model_dump(),
                    }
                )
    return {
        "models": rows,
        "transcription": {
            "items": transcription,
            "max_input_bytes": 100 * 1024**2,
            "accepted_extensions": [".wav", ".mp3", ".m4a", ".ogg", ".flac"],
            "accepted_mime_types": [
                "audio/wav",
                "audio/mpeg",
                "audio/mp4",
                "audio/ogg",
                "audio/flac",
            ],
        },
    }


def transcription_parameters(catalog, params):
    from .contracts import CoreError
    from .extracted.apps.model_service.transcription.parameters import (
        TranscriptionParameterError,
        resolve_parameters,
    )
    from .extracted.apps.model_service.transcription.support import (
        max_transcription_input_bytes,
        resolve_transcription_language,
        resolve_transcription_target_language,
    )
    from .extracted.contracts.model_service.transcription import ModelTestTranscriptionParametersIn

    for provider in catalog.view()["providers"]:
        for model in provider["models"]:
            if model["id"] != params.get("model_id"):
                continue
            if (
                not provider["enabled"]
                or not model["enabled"]
                or "speech_recognition" not in model["capabilities"]
            ):
                raise CoreError("识别模型不可用", "model_unavailable", status=409)
            model, provider = SimpleNamespace(**model), SimpleNamespace(**provider)
            limit = max_transcription_input_bytes(provider)
            if type(params.get("byte_size")) is not int or not 1 <= params["byte_size"] <= limit:
                raise CoreError("语音原件超过当前模型大小限制", "asr_input_too_large", status=413)
            values = params.get("parameters", {})
            if params.get("mode") == "galgame":
                return {
                    "parameters": {"language": values.get("language", "auto")},
                    "byte_limit": limit,
                }
            language = resolve_transcription_language(model, provider, values.get("language"))
            target = resolve_transcription_target_language(
                model, provider, values.get("target_language")
            )
            if values.get("target_language") and target is None:
                raise CoreError(
                    "当前模型不支持所选输出语言", "target_language_unsupported", status=409
                )
            try:
                parameters = resolve_parameters(
                    ModelTestTranscriptionParametersIn(language=language, target_language=target),
                    model,
                    provider,
                )
            except TranscriptionParameterError:
                raise CoreError(
                    "识别参数不受当前模型支持", "transcription_parameters_invalid", status=422
                ) from None
            return {"parameters": parameters.model_dump(mode="json"), "byte_limit": limit}
    raise CoreError("识别模型不可用", "model_unavailable", status=409)
