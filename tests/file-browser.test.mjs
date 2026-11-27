import {test} from 'node:test';
import assert from 'node:assert/strict';
import {browserFile, createFileBrowserAdapter, documentAssetIds, referencedBrowserFiles} from '../web/src/features/resources/fileBrowserAdapter.ts';
const response = value => new Response(JSON.stringify(value), {headers:{'content-type':'application/json'}});
function fixture(overrides={}) {
 const calls=[];
 const request=async(path,init={})=>{
  calls.push({path,...init});
  if(overrides[path])return overrides[path](init);
  if(path==='/v1/core/file-library?limit=200')return response({items:[{id:'same',name:'Core file',revision:7,mime_type:'image/png',byte_size:14}],folders:[],breadcrumbs:[]});
  if(path==='/v1/file-folders')return response({items:[{id:'folder',name:'References',parent_id:null,revision:3}]});
  if(path.startsWith('/v1/files?'))return response({items:[{id:'same',display_name:'App file',revision:12,mime_type:'audio/wav',byte_size:90,folder_id:null}]});
  if(path.startsWith('/v1/app/project-files?'))return response({items:[{source:'core',source_id:'original',resource_id:'snapshot',name:'Receipt file',content_url:'/v1/app/project-file-content?project_id=p&source_id=original'}]});
  if(init.method)return response({});
  throw Error('Unexpected request '+path);
 };
 const adapter=createFileBrowserAdapter({id:'test',request,projects:async()=>[{id:'p',title:'Project'}],projectFiles:{browse:async()=>({items:[{...browserFile({id:'native',displayName:'Native clip',revision:9,mimeType:'video/mp4'},'project','p'),parentId:'inside'},{key:'project:p:inside',id:'inside',name:'Inside',type:'folder',source:'project',mimeType:'',kind:'file',size:0}],breadcrumbs:[]})}});
 return {adapter,calls};
}
test('library preserves source identities, folder hierarchy and real revisions',async()=>{
 const {adapter}=fixture();const page=await adapter.browse({});
 assert.equal(page.items.length,3);
 assert.equal(new Set(page.items.map(item=>item.key)).size,3);
 assert.equal(page.items.find(item=>item.name==='Core file').revision,7);
 assert.equal(page.items.find(item=>item.name==='App file').revision,12);
 assert.equal(page.items.find(item=>item.type==='folder').id,'folder');
});
test('a source failure is reported while the other source stays usable',async()=>{
 const {adapter}=fixture({'/v1/core/file-library?limit=200':()=>new Response('offline',{status:503})});
 const page=await adapter.browse({});assert(page.items.some(item=>item.name==='App file'));assert.match(page.warnings.join(' '),/503/);
});
test('native project folders and archived attachments are both navigable',async()=>{
 const {adapter}=fixture();const root=await adapter.browse({projectId:'p'});
 assert(root.items.some(item=>item.id==='snapshot'));
 const nested=await adapter.browse({projectId:'p',folderId:'inside'});
 assert.equal(nested.items[0].id,'native');assert.equal(nested.breadcrumbs[0].name,'Inside');
});
test('moving to root uses the native root flag and the actual revision',async()=>{
 const {adapter,calls}=fixture();await adapter.move(browserFile({id:'f',revision:29},'application'),null);
 const call=calls.at(-1);assert.equal(call.path,'/v1/files/f');assert.deepEqual(JSON.parse(call.body),{expected_revision:29,folder_id:null,move_to_root:true});
});
test('removing a project attachment only detaches its reference',async()=>{
 const {adapter,calls}=fixture();const page=await adapter.browse({projectId:'p'});const item=page.items.find(item=>item.attachment);
 await adapter.remove(item);const call=calls.at(-1);assert.equal(call.path,'/v1/app/project-files');assert.equal(call.method,'DELETE');assert.deepEqual(JSON.parse(call.body),{project_id:'p',source:'core',source_id:'original'});
});
test('cross-source moves are rejected before any request',async()=>{
 const {adapter,calls}=fixture();await assert.rejects(adapter.move(browserFile({id:'a',revision:2},'core'),{...browserFile({id:'b',revision:3},'application'),type:'folder'}),/同一来源/);assert.equal(calls.length,0);
});
test('document references are explicit and external links do not become local files',()=>{
 const ids=documentAssetIds({content:[{type:'image',attrs:{assetId:'a'}},{asset_id:'b'},{src:'/v1/files/c/content'},{href:'https://evil.example/v1/files/private/content'},{arbitrary:'d'}]});
 assert.deepEqual([...ids].sort(),['a','b','c']);
});
test('document media uses bounded content reads, real response metadata and read-only actions',async()=>{
 let cancelled=false;
 const items=await referencedBrowserFiles(async(path,init)=>{
  assert.equal(path,'/v1/files/a/content');assert.equal(init.headers.Range,'bytes=0-0');
  return new Response(new ReadableStream({cancel(){cancelled=true;}}),{headers:{'content-type':'image/png','content-length':'300','content-disposition':"inline; filename*=UTF-8''scene%20one.png"}});
 },[{asset_id:'a'}],'p');
 assert.equal(items[0].name,'scene one.png');assert.equal(items[0].size,300);assert.equal(items[0].revision,undefined);assert.equal(items[0].canRemove,false);assert(cancelled);
});
test('directory imports preserve relative paths and call the published native API',async()=>{
 const {adapter,calls}=fixture();const file=new File(['data'],'scene.txt',{type:'text/plain'});Object.defineProperty(file,'webkitRelativePath',{value:'folder/scene.txt'});
 await adapter.importDirectory([file],{source:'application',folderId:'parent'},new AbortController().signal);
 const call=calls.at(-1);assert.equal(call.path,'/v1/file-library/folder-entry-imports');assert.equal(call.body.get('relative_paths'),'folder/scene.txt');assert.equal(call.body.get('parent_folder_id'),'parent');
});
test('aborted browsing never produces a successful empty library',async()=>{
 const controller=new AbortController();controller.abort();const {adapter}=fixture();await assert.rejects(adapter.browse({},controller.signal),{name:'AbortError'});
});
test('native folder creation declares the library scope',async()=>{
 const {adapter,calls}=fixture();await adapter.createFolder('Scenes',{});
 assert.equal(calls.at(-1).path,'/v1/file-folders');assert.equal(JSON.parse(calls.at(-1).body).scope,'library');
});
test('interrupted Core imports reuse their durable receipt',async()=>{
 const storage=new Map();Object.defineProperty(globalThis,'sessionStorage',{configurable:true,value:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}});
 const ids=[];let attempt=0;
 const request=async(path,init)=>{
  if(path.startsWith('/v1/core/file-library/imports/'))return response({status:'pending'});
  if(path==='/v1/core/file-library/archive-imports'){
   ids.push(init.body.get('request_id'));return ++attempt===1?new Response('{}',{status:503}):response({files:1,folders:0});
  }
  throw Error('Unexpected request '+path);
 };
 const adapter=createFileBrowserAdapter({id:'test',request,projects:async()=>[],projectFiles:{browse:async()=>({items:[],breadcrumbs:[]})}});
 const file=new File(['zip'],'same.zip');
 try {
  await assert.rejects(adapter.importArchive(file,{source:'core'},new AbortController().signal),/已完成部分不会重复/);
  await adapter.importArchive(file,{source:'core'},new AbortController().signal);
  assert.equal(ids.length,2);assert.equal(ids[0],ids[1]);assert.equal(storage.size,0);
 }finally{delete globalThis.sessionStorage;}
});
