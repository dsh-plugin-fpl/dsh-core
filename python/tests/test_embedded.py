"""Public embedded consumer contracts; no listening socket or external provider."""

import importlib
from pathlib import Path

import pytest

from fpl_core import CoreError, CoreHost, CoreOptions


def test_constructing_public_host_does_not_create_state(tmp_path):
    importlib.import_module("fpl_core")
    root = tmp_path / "not-created"
    CoreHost(CoreOptions("visual-workshop", "Visual", root))
    assert not root.exists()


def test_trusted_launcher_relocates_only_with_existing_product_credential(tmp_path):
    first = CoreHost(CoreOptions('visual-workshop', 'Visual', tmp_path, origin='http://127.0.0.1:19360'))
    receipt = first.provision_product()
    account = first.accounts.initial['id']
    second = CoreHost(CoreOptions('visual-workshop', 'Visual', tmp_path, origin='http://127.0.0.1:19361'))
    with pytest.raises(CoreError):
        second.provision_product(credential='wrong-credential-which-is-long-enough', relocate=True)
    assert second.accounts.client('visual-workshop')['origin'] == receipt['origin']
    moved = second.provision_product(credential=receipt['token'], relocate=True)
    assert moved['origin'] == 'http://127.0.0.1:19361'
    assert moved['token'] == receipt['token']
    assert second.accounts.has_grant(account, 'visual-workshop')


@pytest.mark.asyncio
async def test_sessions_accounts_resource_scope_and_restart(tmp_path):
    options = CoreOptions("visual-workshop", "Visual", tmp_path / "embedded")
    async with CoreHost(options) as core:
        token, _ = core.accounts.issue(core.accounts.initial["id"])
        hello = await core.call(token, "hello", {"protocol": 1})
        assert hello["protocol"] == 1
        bridge_id = hello["bridge_id"]
        resource = await core.call(token, "resource.put", {
            "name": "sample.txt", "mime_type": "text/plain", "data_base64": "aGVsbG8=",
        })
        assert resource["id"]
        other = core.accounts.create("second", "Second", "long-isolated-password", invite=core.accounts.invitation())
        other_token, _ = core.accounts.issue(other["id"])
        with pytest.raises(CoreError, match="不存在"):
            await core.call(other_token, "resource.read", {"id": resource["id"]})
        with pytest.raises(CoreError) as denied:
            await core.call(token, "capabilities", {"application_id": "another-product"})
        assert denied.value.code == "forbidden"
        core.accounts.revoke(token)
        with pytest.raises(CoreError) as denied:
            await core.call(token, "capabilities", {})
        assert denied.value.code == "unauthorized"
        token, _ = core.accounts.issue(core.accounts.initial["id"])
    async with CoreHost(options) as core:
        assert (await core.call(token, "hello", {"protocol": 1}))["bridge_id"] == bridge_id
        assert await core.call(token, "resource.read", {"id": resource["id"]})
