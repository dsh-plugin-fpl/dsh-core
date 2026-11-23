import argparse
import asyncio
import base64
import json
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn

from dsh_core_worker.service import create_app

parser = argparse.ArgumentParser()
parser.add_argument('--root', type=Path, required=True)
parser.add_argument('--port', type=int, required=True)
parser.add_argument('--web-root', type=Path)
parser.add_argument('--mock-models', action='store_true')
args = parser.parse_args()
app = create_app(args.root, args.web_root, origin=f'http://127.0.0.1:{args.port}')
pair = app.state.access.pair('DSH')
(args.root / 'fixture-pair.json').write_text(json.dumps(pair))
(args.root / 'fixture-pair.json').chmod(0o600)
(args.root / 'fixture-launch.txt').write_text(app.state.access.ticket())
(args.root / 'fixture-launch.txt').chmod(0o600)
if args.mock_models:
    original = app.router.lifespan_context
    @asynccontextmanager
    async def fixture_lifespan(application):
        async with original(application):
            async def execute(task, request, connection, emit, scratch):
                if request.capability in ('chat', 'vision', 'tools'):
                    for text in ['独立 Core ', '测试回复，已保留会话历史。']:
                        await emit('assistant_delta', {'text': text})
                        await asyncio.sleep(.05)
                    return {'usage': {'input_tokens': 11, 'output_tokens': 9, 'total_tokens': 20}}
                image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==')
                resource = application.state.runtime.resources.put(image, 'fixture.png', 'image/png', request.application_id)
                return {'resources': [resource]}
            application.state.runtime.models.execute = execute
            yield
    app.router.lifespan_context = fixture_lifespan
uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False, log_level='warning')
