import json

from fastapi.testclient import TestClient

from dsh_core_worker.generation_catalog import generation_options
from dsh_core_worker.service import create_app


def provider(kind, slug, capability):
    return {
        "id": "provider",
        "name": "Provider",
        "kind": kind,
        "enabled": True,
        "models": [
            {
                "id": "model",
                "slug": slug,
                "label": "Model",
                "description": "",
                "enabled": True,
                "capabilities": [capability],
            }
        ],
    }


def test_catalog_uses_provider_sizes_seedance_limits_and_workflow_parameters():
    rows = generation_options(
        [
            provider("volcengine_image", "doubao-seedream-4-5-251128", "image_generation"),
            provider("volcengine_video", "doubao-seedance-2-5-260628", "video_generation"),
            provider("zealman_video", "U17-9图1视频3音频V1-api", "video_generation"),
        ],
        upload_configured=True,
    )["models"]
    assert rows[0]["sizes"][0] == "2048x2048"
    assert "1024x1024" not in rows[0]["sizes"]
    assert rows[0]["max_reference_images"] == 14 and rows[0]["supports_seed"] is False
    assert rows[1]["max_reference_images"] == 30
    assert rows[1]["video_capabilities"]["duration_max"] == 30
    assert rows[1]["video_capabilities"]["reference_video_upload_configured"] is True
    assert rows[2]["workflow_capabilities"]["reference_limits"]["image"] == 9


def test_unsupported_or_disabled_generation_models_are_not_advertised():
    row = provider("volcengine_image", "unsupported-slug", "image_generation")
    assert generation_options([row]) == {"models": []}
    row = provider("volcengine_image", "doubao-seedream-4-5-251128", "image_generation")
    row["enabled"] = False
    assert generation_options([row]) == {"models": []}


def test_paired_catalog_and_model_test_page_share_metadata_without_credentials(tmp_path):
    origin = "http://testserver"
    app = create_app(tmp_path / "core", origin=origin)
    with TestClient(app) as client:
        login = client.post(
            "/v1/session", json={"ticket": app.state.access.ticket()}, headers={"Origin": origin}
        ).json()
        client.headers.update({"Origin": origin, "X-Core-CSRF": login["csrf"]})
        secret = "isolated-provider-secret"
        created = client.post(
            "/v1/model-providers",
            json={
                "name": "Seedream",
                "kind": "volcengine_image",
                "base_url": "https://example.com",
                "api_key": secret,
                "expected_catalog_revision": 0,
            },
        )
        assert created.status_code == 200, created.text
        identity = created.json()["provider"]["id"]
        model = client.post(
            "/v1/model-providers/" + identity + "/models",
            json={
                "slug": "doubao-seedream-4-5-251128",
                "label": "Seedream 4.5",
                "capabilities": ["image_generation"],
                "categories": [],
                "enabled": True,
                "expected_catalog_revision": 1,
            },
        )
        assert model.status_code == 200, model.text
        pair = client.post("/v1/bridges", json={"name": "Visual"}).json()
        with client.websocket_connect(
            "/v1/bridge", headers={"Authorization": "Bearer " + pair["token"]}
        ) as socket:
            socket.send_json(
                {"jsonrpc": "2.0", "id": "catalog", "method": "capabilities", "params": {}}
            )
            response = socket.receive_json()
        assert "error" not in response, response
        catalog = response["result"]["generation_options"]
        browser = client.get("/v1/model-test/generation-options").json()
        assert browser["items"] == catalog["models"]
        assert catalog["models"][0]["id"] == model.json()["model"]["id"]
        assert catalog["models"][0]["sizes"][0] == "2048x2048"
        encoded = json.dumps(response)
        assert secret not in encoded and "https://example.com" not in encoded
        assert "credential_ref" not in encoded
