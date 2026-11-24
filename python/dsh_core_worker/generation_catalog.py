"""Credential-free generation parameters shared by the dashboard and paired applications."""

from .extracted.apps.model_service.providers import autodl_workflows
from .extracted.apps.model_service.providers.seedance_parameters import video_capabilities
from .extracted.apps.model_service.providers.volcengine_image import SEEDREAM_MODEL, SEEDREAM_SIZES
from .extracted.apps.model_service.providers.zealman_workflows import WORKFLOWS, parameter_options

KINDS = {
    "volcengine_image": "image",
    "volcengine_video": "video",
    "zealman_video": "video",
    "autodl_video": "video",
}


def generation_options(providers, *, upload_configured=False):
    rows = []
    for provider in providers:
        provider_kind = provider["kind"]
        kind = KINDS.get(provider_kind)
        if not provider["enabled"] or kind is None:
            continue
        for model in provider["models"]:
            slug = model["slug"]
            if not model["enabled"] or kind + "_generation" not in model["capabilities"]:
                continue
            if provider_kind == "volcengine_image" and slug != SEEDREAM_MODEL:
                continue
            if provider_kind == "zealman_video" and slug not in WORKFLOWS:
                continue
            if provider_kind == "autodl_video" and slug not in autodl_workflows.WORKFLOWS:
                continue
            if provider_kind == "volcengine_video" and slug not in {
                "doubao-seedance-2-0-260128",
                "doubao-seedance-2-5-260628",
            }:
                continue
            sizes = list(
                SEEDREAM_SIZES
                if provider_kind == "volcengine_image"
                else ()
            )
            if kind == "image" and not sizes:
                continue
            caps = video_capabilities(provider_kind, slug, upload_configured=upload_configured)
            rows.append(
                {
                    "id": model["id"],
                    "provider_id": provider["id"],
                    "provider_name": provider["name"],
                    "provider_kind": provider_kind,
                    "kind": kind,
                    "slug": slug,
                    "label": model["label"] or slug,
                    "description": model["description"],
                    "sizes": sizes,
                    "max_reference_images": 14
                    if provider_kind == "volcengine_image"
                    else 30
                    if provider_kind == "volcengine_video" and slug == "doubao-seedance-2-5-260628"
                    else 9
                    if provider_kind in {"zealman_video", "volcengine_video", "autodl_video"}
                    else 1,
                    "supports_seed": provider_kind not in {"volcengine_image", "volcengine_video"},
                    "max_prompt_length": 20000
                    if provider_kind == "autodl_video"
                    else 6000
                    if provider_kind == "zealman_video"
                    else 2000,
                    "video_capabilities": caps.model_dump(mode="json") if caps else None,
                    "workflow_capabilities": parameter_options(slug)
                    if provider_kind == "zealman_video"
                    else None,
                    "reference_mime_types": ["image/jpeg", "image/png", "image/webp"],
                }
            )
    return {"models": rows}
