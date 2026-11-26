import json
import sqlite3

import pytest
from cryptography.fernet import Fernet

from fpl_core import CoreHost, CoreOptions
from fpl_core.migration import export_product, verify_snapshot


@pytest.mark.asyncio
async def test_product_snapshot_scopes_and_reencrypts_without_replaying(tmp_path):
    root = tmp_path / 'source'
    async with CoreHost(CoreOptions('visual-workshop','Visual',root)) as core:
        token, _ = core.accounts.issue(core.accounts.initial['id'])
        owner = core.accounts.initial['id']
        bridge = (await core.call(token,'hello',{'protocol':1}))['bridge_id']
        resource = await core.call(token,'resource.put',{'name':'sample','mime_type':'text/plain','data_base64':'aGVsbG8='})
        store = core.app.state.runtime.store
        secret = core.app.state.access.cipher.encrypt(b'isolated-provider-secret').decode()
        store.save_config('credential:fixture',secret,0)
        original_key = (root / 'access/secret.key').read_bytes()
        with pytest.raises(ValueError, match='停止'):
            export_product(root,tmp_path/'busy','visual-workshop','dsh-visual-workshop',{owner:[bridge]})
    snapshot = tmp_path / 'snapshot'
    manifest = export_product(root,snapshot,'visual-workshop','dsh-visual-workshop',{owner:[bridge]})
    assert verify_snapshot(snapshot) == manifest
    assert (snapshot/'access/secret.key').read_bytes() != original_key
    with sqlite3.connect(snapshot/'state/core.sqlite3') as db:
        encrypted = json.loads(db.execute("SELECT value FROM configs WHERE id='credential:fixture'").fetchone()[0])
        assert Fernet((snapshot/'access/secret.key').read_bytes()).decrypt(encrypted.encode()) == b'isolated-provider-secret'
        assert json.loads(db.execute('SELECT value FROM resources WHERE id=?',(resource['id'],)).fetchone()[0])['application_id'] == manifest['scope']
    with sqlite3.connect(snapshot/'identity/identity.db') as db:
        assert db.execute('SELECT count(*) FROM account_session').fetchone()[0] == 0
    assert (root/'access/secret.key').read_bytes() == original_key
    (snapshot/'state/core.sqlite3').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='校验'):
        verify_snapshot(snapshot)
