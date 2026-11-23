import base64

import pytest
from fastapi.testclient import TestClient

from dsh_core_worker.service import create_app


@pytest.fixture
def service(tmp_path):
    app = create_app(tmp_path / "core", origin="http://testserver")
    with TestClient(app) as client:
        ticket = app.state.access.ticket()
        response = client.post(
            "/v1/session", json={"ticket": ticket}, headers={"Origin": "http://testserver"}
        )
        assert response.status_code == 200
        client.headers.update(
            {"Origin": "http://testserver", "X-Core-CSRF": response.json()["csrf"]}
        )
        yield app, client


def add_model(client, capability="chat", kind="openai", slug="test-model"):
    response = client.post(
        "/v1/model-providers",
        json={
            "name": "Fixture",
            "kind": kind,
            "base_url": "https://api.example.com",
            "api_key": "fixture-private-secret",
            "expected_catalog_revision": 0,
        },
    )
    assert response.status_code == 200, response.text
    provider = response.json()["provider"]
    response = client.post(
        "/v1/model-providers/" + provider["id"] + "/models",
        json={
            "slug": slug,
            "label": "Fixture model",
            "capabilities": [capability],
            "categories": [],
            "enabled": True,
            "expected_catalog_revision": 1,
        },
    )
    assert response.status_code == 200, response.text
    return provider, response.json()["model"]


def test_session_ticket_is_single_use_and_origin_and_csrf_required(service):
    app, client = service
    ticket = app.state.access.ticket()
    assert client.post("/v1/session", json={"ticket": ticket}).status_code == 200
    assert client.post("/v1/session", json={"ticket": ticket}).status_code == 401
    assert (
        client.post(
            "/v1/bridges", json={"name": "DSH"}, headers={"X-Core-CSRF": "wrong"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/bridges", json={"name": "DSH"}, headers={"Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )
    assert client.get("/v1/state", headers={"Host": "evil.example"}).status_code == 403
    client.cookies.clear()
    assert client.get("/v1/state").status_code == 401
    assert client.get("/health").status_code == 200


def test_catalog_atomic_conflicts_defaults_and_secret_redaction(service):
    app, client = service
    provider, model = add_model(client)
    response = client.get("/v1/model-providers")
    assert "fixture-private-secret" not in response.text
    assert response.json()["providers"][0]["has_api_key"]
    assert b"fixture-private-secret" not in (app.state.access.root.parent / "state/core.sqlite3").read_bytes()
    response = client.put(
        "/v1/model-catalog/defaults",
        json={"expected_revision": 2, "default_model_ids": {"chat": model["id"]}},
    )
    assert response.status_code == 200, response.text
    response = client.patch(
        "/v1/model-providers/" + provider["id"],
        json={
            "expected_catalog_revision": 1,
            "name": "stale",
            "kind": "openai",
            "base_url": "https://api.example.com",
        },
    )
    assert response.status_code == 409
    assert response.json()["current"]["revision"] == 3
    assert client.get("/v1/model-providers").json()["providers"][0]["name"] == "Fixture"


def test_independent_task_and_conversation_history_survive_browser_and_service_restart(
    service, monkeypatch
):
    app, client = service
    _provider, model = add_model(client)
    seen = []

    async def execute(task, request, connection, emit, scratch):
        seen.append([m.model_dump() for m in request.messages])
        await emit("assistant_delta", {"text": "独立服务回复"})
        return {"usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}}

    monkeypatch.setattr(app.state.runtime.models, "execute", execute)
    conversation = client.post(
        "/v1/model-test/conversations", json={"model_id": model["id"]}
    ).json()
    url = "/v1/model-test/conversations/" + conversation["id"]
    response = client.post(
        url + "/messages/stream",
        json={"expected_revision": conversation["revision"], "content": "第一条"},
    )
    assert response.status_code == 200, response.text
    assert '"type": "done"' in response.text
    saved = client.get(url).json()
    assert saved["messages"][-1]["content"] == "独立服务回复"
    response = client.post(
        url + "/messages/stream", json={"expected_revision": saved["revision"], "content": "第二条"}
    )
    assert response.status_code == 200
    assert [m["role"] for m in seen[-1]] == ["user", "assistant", "user"]
    logs = client.get("/v1/llm-call-logs").json()
    assert len(logs["items"]) == 2
    assert logs["items"][0]["total_tokens"] == 7
    assert client.get("/v1/llm-call-logs/stats").json()["calls_today"] == 2


def test_task_idempotency_and_events_without_harness(service, monkeypatch):
    app, client = service
    provider, model = add_model(client)

    async def execute(task, request, connection, emit, scratch):
        await emit("assistant_delta", {"text": "result"})
        return {}

    monkeypatch.setattr(app.state.runtime.models, "execute", execute)
    body = {
        "request_id": "same-request",
        "application_id": "core",
        "connection_id": provider["id"],
        "model": model["slug"],
        "prompt": "once",
    }
    first = client.post("/v1/tasks", json=body)
    assert first.status_code == 200, first.text
    second = client.post("/v1/tasks", json=body)
    assert second.json()["id"] == first.json()["id"]
    assert client.post("/v1/tasks", json={**body, "prompt": "changed"}).status_code == 409
    events = client.get("/v1/tasks/" + first.json()["id"] + "/events").json()["items"]
    assert any(e["kind"] == "assistant_delta" for e in events)
    assert (
        client.get(
            "/v1/tasks/" + first.json()["id"] + "/events?after=" + str(events[-1]["seq"])
        ).json()["items"]
        == []
    )


def test_resource_chunks_preview_and_integrity(service):
    _app, client = service

    def rpc(method, params):
        response = client.post("/v1/rpc", json={"method": method, "params": params})
        assert response.status_code == 200, response.text
        return response.json()

    resource = rpc(
        "resource.put",
        {
            "name": "note.html",
            "mime_type": "text/html",
            "data_base64": base64.b64encode(b"<script>bad()</script>").decode(),
        },
    )
    response = client.get("/v1/resources/" + resource["id"] + "/content")
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-security-policy"] == "sandbox"
    upload = rpc(
        "resource.upload.begin", {"name": "text", "mime_type": "text/plain", "byte_size": 5}
    )
    params = {"id": upload["id"], "offset": 0, "data_base64": base64.b64encode(b"hello").decode()}
    rpc("resource.upload.append", params)
    rpc("resource.upload.append", params)
    result = rpc("resource.upload.finish", {"id": upload["id"]})
    assert client.get("/v1/resources/" + result["id"] + "/content").content == b"hello"
    assert client.post("/v1/rpc", json={"method": "host.event", "params": {}}).status_code == 403


def test_pairing_bridge_scopes_and_revocation(service):
    _app, client = service
    pair = client.post("/v1/bridges", json={"name": "Test DSH"}).json()
    assert "token" not in client.get("/v1/bridges").text
    with client.websocket_connect(
        "/v1/bridge", headers={"Authorization": "Bearer " + pair["token"]}
    ) as ws:

        def rpc(method, params):
            ws.send_json({"jsonrpc": "2.0", "id": method, "method": method, "params": params})
            return ws.receive_json()

        assert rpc("hello", {"protocol": 1})["result"]["protocol"] == 1
        assert rpc("credential.resolve", {"ref": "secret"})["error"]["code"] == "forbidden"
        assert rpc("application.register", {"id": "notes", "name": "Notes"})["result"]["registered"]
        resource = rpc(
            "resource.put",
            {
                "application_id": "notes",
                "name": "one.txt",
                "mime_type": "text/plain",
                "data_base64": "b25l",
            },
        )["result"]
        assert resource["application_id"].startswith("b-" + pair["id"])
        assert rpc("resource.read", {"id": resource["id"]})["result"]["data_base64"] == "b25l"
        assert client.get("/v1/bridges").json()["items"][0]["connected"]
        client.delete("/v1/bridges/" + pair["id"])
    assert client.get("/v1/bridges").json()["items"] == []


def test_catalog_empty_agents_local_models_and_all_read_pages(service):
    _app, client = service
    for url in [
        "/v1/model-categories",
        "/v1/model-test/conversations",
        "/v1/model-test/generation-options",
        "/v1/model-test/generation-tasks",
        "/v1/model-test/transcription-options",
        "/v1/model-test/transcription-tasks",
        "/v1/model-inference/options",
        "/v1/model-inference/tasks",
        "/v1/local-models",
        "/v1/code-agents",
        "/v1/resources",
        "/v1/tasks",
        "/v1/model-test/realtime-options",
    ]:
        response = client.get(url)
        assert response.status_code == 200, (url, response.text)


def test_conversation_and_encrypted_credentials_reopen(tmp_path):
    root = tmp_path / "core"
    app = create_app(root, origin="http://testserver")
    with TestClient(app) as client:
        ticket = app.state.access.ticket()
        login = client.post(
            "/v1/session", json={"ticket": ticket}, headers={"Origin": "http://testserver"}
        ).json()
        client.headers.update({"Origin": "http://testserver", "X-Core-CSRF": login["csrf"]})
        _provider, model = add_model(client)
        conversation = client.post(
            "/v1/model-test/conversations", json={"model_id": model["id"]}
        ).json()
    second = create_app(root, origin="http://testserver")
    with TestClient(second) as client:
        login = client.post(
            "/v1/session",
            json={"ticket": second.state.access.ticket()},
            headers={"Origin": "http://testserver"},
        ).json()
        client.headers.update({"Origin": "http://testserver", "X-Core-CSRF": login["csrf"]})
        assert (
            client.get("/v1/model-test/conversations/" + conversation["id"]).json()["model_id"]
            == model["id"]
        )
        assert client.get("/v1/model-providers").json()["providers"][0]["has_api_key"]


def test_upload_finish_replays_after_lost_receipt_and_partial_commit(service, monkeypatch):
    app, client = service
    def rpc(method, params):
        return client.post('/v1/rpc', json={'method': method, 'params': params})
    upload = rpc('resource.upload.begin', {'name': 'receipt.txt', 'mime_type': 'text/plain', 'byte_size': 3}).json()
    assert rpc('resource.upload.append', {'id': upload['id'], 'offset': 0, 'data_base64': 'b25l'}).status_code == 200
    original = app.state.runtime.store.save_config
    attempts = 0
    def commit(key, value, revision):
        nonlocal attempts
        if key.startswith('upload-receipt:'):
            attempts += 1
            if attempts == 1:
                raise ValueError('injected receipt failure')
        return original(key, value, revision)
    monkeypatch.setattr(app.state.runtime.store, 'save_config', commit)
    assert rpc('resource.upload.finish', {'id': upload['id']}).status_code == 422
    result = rpc('resource.upload.finish', {'id': upload['id']})
    assert result.status_code == 200, result.text
    assert rpc('resource.upload.finish', {'id': upload['id']}).json() == result.json()
    assert len(client.get('/v1/resources').json()['items']) == 1
    assert rpc('resource.upload.status', {'id': upload['id']}).json()['completed']


def test_failed_credential_encryption_does_not_commit_provider_changes(service, monkeypatch):
    app, client = service
    provider, _model = add_model(client)
    def fail(_value):
        raise ValueError('injected encryption failure')
    monkeypatch.setattr(app.state.credentials, 'encrypted', fail)
    response = client.patch('/v1/model-providers/' + provider['id'], json={
        'expected_catalog_revision': 2, 'name': 'Should not commit', 'kind': 'openai',
        'base_url': 'https://changed.example.com', 'api_key': 'replacement-secret'})
    assert response.status_code == 422
    catalog = client.get('/v1/model-providers').json()
    assert catalog['revision'] == 2
    assert catalog['providers'][0]['name'] == 'Fixture'
    assert catalog['providers'][0]['base_url'] == 'https://api.example.com'
    assert catalog['providers'][0]['has_api_key']


def test_catalog_protocol_rejects_bad_fields_without_mutating_state(service):
    _app, client = service
    response = client.post('/v1/model-providers', json={'name': 'bad', 'kind': 'unsupported', 'expected_catalog_revision': 0})
    assert response.status_code == 422
    assert client.get('/v1/model-providers').json()['revision'] == 0
    response = client.post('/v1/rpc', json={'method': 'resource.put', 'params': {
        'name': 'bad', 'mime_type': 'text/html\r\nX-Injected: yes', 'data_base64': 'b25l'}})
    assert response.status_code == 422
    assert client.get('/v1/resources').json()['items'] == []


def test_plugin_application_catalog_registration_scope_offline_and_uninstall(service):
    app, client = service
    pair = client.post('/v1/bridges', json={'name': 'Application DSH'}).json()
    entry = {'id': 'notes', 'name': '插件笔记', 'dashboard': {
        'description': '由已安装插件提供', 'category': 'knowledge', 'capabilities': ['笔记'],
        'url': 'http://127.0.0.1:19320/notes', 'package_name': 'dsh-notes', 'version': '1.0.0'}}
    with client.websocket_connect('/v1/bridge', headers={'Authorization': 'Bearer '+pair['token']}) as ws:
        def rpc(method, params):
            ws.send_json({'jsonrpc':'2.0','id':method,'method':method,'params':params})
            return ws.receive_json()
        assert rpc('applications.sync', {'applications':[{'id':'internal','name':'Internal'},entry]})['result']['synchronized']
        catalog = client.get('/v1/applications').json()['items']
        assert len(catalog) == 1
        assert catalog[0]['id'] == 'b-'+pair['id']+'-notes'
        assert catalog[0]['status'] == 'ready'
        assert catalog[0]['package_name'] == 'dsh-notes'
        assert 'tools' not in catalog[0]
        assert rpc('application.unregister', {'id':'notes'})['result']['registered'] is False
        assert client.get('/v1/applications').json()['items'] == []
        rpc('application.register', entry)
    assert client.get('/v1/applications').json()['items'][0]['status'] == 'offline'
    # A full snapshot on reconnect removes plugins uninstalled while disconnected.
    with client.websocket_connect('/v1/bridge', headers={'Authorization':'Bearer '+pair['token']}) as ws:
        ws.send_json({'jsonrpc':'2.0','id':'sync','method':'applications.sync','params':{'applications':[]}})
        assert ws.receive_json()['result']['synchronized']
        assert client.get('/v1/applications').json()['items'] == []
    client.delete('/v1/bridges/'+pair['id'])
    assert client.get('/v1/applications').json()['items'] == []


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'data:text/html,unsafe', 'https://example.com/app', 'http://127.0.0.1@evil.example/app', 'http://127.0.0.1\\evil.example/', 'http://127.0.0.1/\nunsafe'])
def test_plugin_dashboard_rejects_untrusted_urls_atomically(service, url):
    _app, client = service
    pair = client.post('/v1/bridges', json={'name':'DSH'}).json()
    with client.websocket_connect('/v1/bridge', headers={'Authorization':'Bearer '+pair['token']}) as ws:
        ws.send_json({'jsonrpc':'2.0','id':'bad','method':'applications.sync','params':{'applications':[
            {'id':'valid','name':'Valid','dashboard':{'url':'http://127.0.0.1:19320/'}},
            {'id':'bad','name':'Bad','dashboard':{'url':url}}]}})
        assert 'error' in ws.receive_json()
        assert client.get('/v1/applications').json()['items'] == []
