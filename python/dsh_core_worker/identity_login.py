"""Browser handoff into the existing application authorization code flow."""

import json


def local_authorization_html(target):
    encoded = json.dumps(target).replace("<", "\\u003c")
    return """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<p role="status">正在连接本机所有者…</p><script>
fetch('/v1/session/local', {method:'POST'}).then(response => {
  if (!response.ok) throw Error();
  location.replace(TARGET);
}).catch(() => {
  document.querySelector('p').textContent = '无法连接本机，请重新打开 Core 看板。';
});
</script></html>""".replace("TARGET", encoded)
