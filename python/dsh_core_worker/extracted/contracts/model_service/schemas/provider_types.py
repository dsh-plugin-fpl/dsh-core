"""Provider identifiers shared by model and workshop HTTP schemas."""

from typing import Literal

ProviderKind = Literal[
    "local_qwen_tts",
    "local_zonos_tts",
    "local_relayvsr",
    "local_ffmpeg",
    "volcengine_image",
    "openai",
    "deepseek",
    "anthropic",
    "volcengine_video",
    "zealman_video",
    "autodl_video",
]

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
