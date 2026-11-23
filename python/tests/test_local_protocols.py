import hashlib
import io
import json
import sys
from types import SimpleNamespace

import httpx
import pytest
from dsh_core_worker.extracted.host.local_models import worker
from dsh_core_worker.extracted.host.local_models.downloads import fetch_file


def test_worker_writes_wav_to_extensionless_asset_key(monkeypatch, capsys):
    writes = []
    mx = SimpleNamespace(get_peak_memory=lambda: 100)
    model = SimpleNamespace(
        generate=lambda **_kwargs: [SimpleNamespace(audio=[0.0, 0.1], sample_rate=24000)]
    )
    modules = {
        "mlx": SimpleNamespace(core=mx),
        "mlx.core": mx,
        "numpy": SimpleNamespace(asarray=lambda value: value, concatenate=lambda parts: parts[0]),
        "soundfile": SimpleNamespace(write=lambda *args, **kwargs: writes.append((args, kwargs))),
        "mlx_audio.tts.utils": SimpleNamespace(load_model=lambda _path: model),
    }
    for name, value in modules.items():
        monkeypatch.setitem(sys.modules, name, value)
    monkeypatch.setattr(sys, "argv", ["worker.py", "fixed-model"])
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "text": "你好",
                    "language": "Chinese",
                    "reference_path": "reference.wav",
                    "reference_text": "sample",
                    "output_path": "opaque-asset-key",
                }
            )
            + "\n"
        ),
    )
    worker.run()
    assert writes[0][0][0] == "opaque-asset-key"
    assert writes[0][1] == {"format": "WAV", "subtype": "PCM_16"}
    assert json.loads(capsys.readouterr().out)["ok"] is True


async def test_download_resume_and_hash_rejection(tmp_path):
    data = b"known model contents"
    target = tmp_path / "weights"
    target.with_name("weights.part").write_bytes(data[:6])
    item = {
        "url": "https://files.invalid/weights",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }

    def response(request):
        assert request.headers["range"] == "bytes=6-"
        return httpx.Response(
            206, headers={"content-range": f"bytes 6-{len(data) - 1}/{len(data)}"}, content=data[6:]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        await fetch_file(client, item, target)
    assert target.read_bytes() == data
    target.unlink()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * len(data)))
    ) as client:
        with pytest.raises(ValueError, match="SHA256"):
            await fetch_file(client, item, target)
    assert not target.exists()
