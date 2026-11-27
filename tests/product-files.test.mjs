import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
const require = createRequire(new URL('../web/package.json', import.meta.url));
const ts = require('typescript');
const source = await readFile(new URL('../web/src/features/resources/productFiles.ts', import.meta.url), 'utf8');
const javascript = ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}}).outputText;
const {loadProductFiles, acceptsFile, readProductFile} = await import('data:text/javascript;base64,'+Buffer.from(javascript).toString('base64'));

function fixture({failApplication=false, denied=false}={}) {
  const calls=[];
  const request=async (path, init) => {
    calls.push(path);
    if(init?.signal?.aborted) throw new DOMException('Aborted','AbortError');
    const url=new URL(path,'http://local');
    if(url.pathname==='/v1/core/file-library/folders') return Response.json([{id:'nested'}]);
    if(url.pathname==='/v1/core/file-library') {
      if(url.searchParams.get('folder_id')) return Response.json({items:[{id:'nested-file',name:'nested.txt',mime_type:'text/plain'}]});
      if(url.searchParams.get('after')) return Response.json({items:[{id:'page2',name:'next.txt'}]});
      return Response.json({items:[{id:'original',name:'same.png',mime_type:'image/png',byte_size:3}],next_cursor:'next'});
    }
    if(path==='/v1/app/legacy-application-files') {
      if(failApplication) throw Error('application unavailable');
      return Response.json({items:[{id:'original',display_name:'same.png',mime_type:'image/png'}]});
    }
    if(path==='/v1/app/file-projects') return Response.json({items:[{id:'project',title:'Project'},{id:'second',title:'Second'}]});
    if(url.pathname==='/v1/app/project-files' && denied) return new Response('forbidden',{status:403});
    const receipts=[
      {id:'original',project_id:'project',source:'core',source_id:'original',name:'same.png',mime_type:'image/png',content_url:'/protected/original'},
      {id:'original',project_id:'second',source:'core',source_id:'original',name:'same.png',mime_type:'image/png',content_url:'/protected/original'},
      {id:'legacy',project_id:'project',source:'application',source_id:'legacy',resource_id:'copy',name:'hidden.wav',mime_type:'audio/wav',content_url:'/protected/copy'},
      {id:'lost',project_id:'deleted-project',source:'core',source_id:'lost',name:'deleted.txt',content_url:'/protected/lost'},
    ];
    if(path==='/v1/app/legacy-file-receipts') return Response.json({items:receipts});
    if(url.pathname==='/v1/app/project-files') return Response.json({items:receipts.filter(r=>r.project_id===url.searchParams.get('project_id'))});
    if(path==='/protected/copy') return new Response('wav-bytes');
    throw Error('Unexpected request: '+path);
  };
  return {request,calls};
}
test('unifies sources without ID/name collisions, follows folders/pages and preserves receipts',async()=>{
  const {request,calls}=fixture(); const result=await loadProductFiles(request);
  assert.equal(result.items.length,5);
  assert.equal(result.items.find(f=>f.key==='core:original').receipts.length,2);
  assert(result.items.some(f=>f.key==='application:original'));
  assert(result.items.some(f=>f.id==='nested-file'));
  assert(!result.items.some(f=>f.id==='lost'));
  assert(calls.some(c=>c.includes('after=next')));
  const copy=result.items.find(f=>f.id==='copy'); assert.equal(copy.content_url,'/protected/copy');
  assert.equal(await (await readProductFile(request,copy)).text(),'wav-bytes');
});
test('project mode uses authorized receipts and never scans global files',async()=>{
  const {request,calls}=fixture(); const result=await loadProductFiles(request,'project');
  assert.equal(result.items.length,2);assert.equal(result.items.find(f=>f.id==='original').content_url,'/protected/original');
  assert(!calls.some(c=>c.startsWith('/v1/core/')));
});
test('denied project cannot fall back to unscoped data',async()=>{
  const {request,calls}=fixture({denied:true});await assert.rejects(loadProductFiles(request,'project'),/403/);assert.equal(calls.length,1);
});
test('partial failure retains successful sources and exposes a retryable warning',async()=>{
  const {request}=fixture({failApplication:true});const result=await loadProductFiles(request);
  assert.equal(result.warnings.length,1);assert.match(result.warnings[0],/工坊素材/);assert(result.items.some(f=>f.id==='copy'));
});
test('picker honors MIME/extension restrictions and aborts stale reads',async()=>{
  assert(acceptsFile({name:'VECTOR.SVG',mime_type:'image/svg+xml'},'image/*'));
  assert(acceptsFile({name:'FILE.DOCX',mime_type:'application/octet-stream'},'.pdf,.docx'));
  assert(!acceptsFile({name:'clip.mp4',mime_type:'video/mp4'},'audio/*'));
  const {request}=fixture();const controller=new AbortController();controller.abort();
  await assert.rejects(loadProductFiles(request,'',controller.signal),{name:'AbortError'});
});
test('embedded previews retain product-authorized content routes and Core requests still remap',async()=>{
  const embeddedSource=await readFile(new URL('../web/src/platform/embedded.ts',import.meta.url),'utf8');
  const code=ts.transpileModule(embeddedSource,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}}).outputText;
  const platform=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
  globalThis.location=new URL('http://local/core/files');globalThis.document={cookie:'product_csrf=fixture-token'};
  try{
    platform.configureCorePlatform({apiPrefix:'/v1/core',routePrefix:'/core',csrfCookie:'product_csrf',product:{name:'Product',projectsUrl:'/apps/product'}});
    for(const path of ['/v1/app/project-file-content?source_id=original','/v1/files/native/download','/v1/core/file-library']){
      assert.equal((await platform.coreRequest(new Request('http://local'+path))).url,'http://local'+path);
    }
    const rpc=await platform.coreRequest(new Request('http://local/v1/rpc',{method:'POST',body:'{}'}));
    assert.equal(rpc.url,'http://local/v1/core/rpc');assert.equal(rpc.headers.get('X-CSRF-Token'),'fixture-token');
    assert.equal(await rpc.text(),'{}');
  }finally{delete globalThis.location;delete globalThis.document;}
});
