"""Verify a built runtime package using only its own Python and web assets."""
import argparse
import base64
import concurrent.futures
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import urllib.request

parser=argparse.ArgumentParser()
parser.add_argument('directory',type=Path)
args=parser.parse_args()
release=args.directory.resolve()
repository=Path(__file__).resolve().parents[1]
output=repository/'.local/release-acceptance'
output.mkdir(parents=True,exist_ok=True)
python=release/'.runtime/worker-venv/bin/python'
environment={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV')}

def command(*values,check=True):
    result=subprocess.run([str(python),'-m','dsh_core_worker.cli',*values],cwd=release,env=environment,capture_output=True,text=True,timeout=30)
    if check and result.returncode:
        raise AssertionError(result.stderr+result.stdout)
    return result

def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0))
        return probe.getsockname()[1]

module=subprocess.check_output([str(python),'-c','import dsh_core_worker; print(dsh_core_worker.__file__)'],cwd=release,env=environment,text=True).strip()
assert Path(module).is_relative_to(release),module
assert (release/'web/dist/index.html').is_file()
assert not (release/'.runtime/core').exists(),'Release must start without business data'
with tempfile.TemporaryDirectory(prefix='state-',dir=output) as temporary:
    root=Path(temporary);port=free_port();origin=f'http://127.0.0.1:{port}'
    options=('--root',str(root),'--port',str(port),'--no-open')
    def call(*values,check=True): return command(*values,*options,check=check)
    def session():
        ticket=subprocess.check_output([str(python),'-c',"from dsh_core_worker.access import Access; import sys; print(Access(sys.argv[1]).ticket())",str(root/'access')],cwd=release,env=environment,text=True).strip()
        opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def api(path,body=None,csrf=None):
            headers={'Origin':origin,'Content-Type':'application/json'}
            if csrf: headers['X-Core-CSRF']=csrf
            request=urllib.request.Request(origin+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
            with opener.open(request,timeout=5) as response: return json.load(response)
        csrf=api('/v1/session',{'ticket':ticket})['csrf']
        return lambda path,body=None:api(path,body,csrf)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda _:call('start'),range(2)))
        first=json.loads(call('status').stdout)
        assert first['running']
        call('start');assert json.loads(call('status').stdout)['pid']==first['pid']
        wrong=command('start','--root',str(root),'--port',str(free_port()),'--no-open',check=False)
        assert wrong.returncode and json.loads(call('status').stdout)['pid']==first['pid']
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',0));occupied.listen()
            conflict=command('start','--root',str(root/'foreign'),'--port',str(occupied.getsockname()[1]),'--no-open',check=False)
            assert conflict.returncode,'Occupied port accepted'
        api=session();assert api('/v1/model-providers')['providers']==[]
        assert api('/v1/applications')['items']==[]
        resource=api('/v1/rpc',{'method':'resource.put','params':{'name':'proof.txt','mime_type':'text/plain','data_base64':base64.b64encode(b'independent package').decode()}})
        call('stop');assert not json.loads(call('status').stdout)['running']
        call('start');api=session()
        saved=api('/v1/rpc',{'method':'resource.read','params':{'id':resource['id']}})
        assert base64.b64decode(saved['data_base64'])==b'independent package'
        result={'package':str(release),'module_inside_package':True,'empty_configuration':True,'repeated_and_concurrent_start':True,'port_conflict_safe':True,'state_survives_restart':True,'stopped_cleanly':True}
    finally:
        call('stop',check=False)
(output/'latest.json').write_text(json.dumps(result,indent=2)+'\n')
print('Independent runtime package: startup, reuse, conflicts, persistence and shutdown passed')
