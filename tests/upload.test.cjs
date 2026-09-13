const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/static/scripts/library-upload.js'), 'utf8');
function setup(fetch) {
  const context = { fetch, FormData: class { constructor() { this.files = []; } append(...args) { this.files.push(args); } }, window: {location:{href:'/l/1?q=kept'}} };
  vm.createContext(context); vm.runInContext(source, context);
  const upload = context.libraryUpload(1);
  upload.$ajax = async (...args) => { upload.refreshed = args; };
  return upload;
}
const file = name => ({ name, isFile:true, file: resolve => resolve({name}) });
const folder = (name, batches) => ({name,isFile:false,createReader:()=>({readEntries:resolve=>resolve(batches.shift() || [])})});
test('directory drop reads every browser batch and preserves full folder paths', async () => {
  const upload=setup();
  const entries=await upload.collect(folder('Manga', [[folder('Volume',[[file('1.jpg')],[file('2.jpg')],[]])],[]]));
  assert.deepEqual(Array.from(entries,x=>x[0]),['Manga/Volume/1.jpg','Manga/Volume/2.jpg']);
});
test('drop submits directory files then refreshes the existing filtered library through AJAX',async()=>{
  let sent;
  const upload=setup(async (url,options)=>{sent={url,options};return {ok:true,json:async()=>({ok:true})};});
  await upload.dropped({dataTransfer:{items:[{webkitGetAsEntry:()=>folder('Manga',[[folder('Volume',[[file('1.jpg')]])]])}],files:[]}});
  assert.equal(sent.url,'/api/lib/1/upload');
  assert.equal(sent.options.body.files[0][2],'Manga/Volume/1.jpg');
  assert.equal(upload.refreshed[0],'/l/1?q=kept');
  assert.equal(upload.refreshed[1].target,'breadcrumb main statusbar');
  assert.equal(upload.uploading,false);
});
test('plain ZIP drop fallback and server failure leave useful error without refreshing',async()=>{
  const upload=setup(async()=>({ok:false,status:409,json:async()=>({error:'Title already exists'})}));
  await upload.dropped({dataTransfer:{items:[],files:[{name:'manga.zip'}]}});
  assert.equal(upload.uploadMessage,'Title already exists');
  assert.equal(upload.refreshed,undefined);
  assert.equal(upload.uploading,false);
});
test('in-flight upload prevents a duplicate submission',async()=>{
  let calls=0,finish;
  const upload=setup(()=>{calls++;return new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>({})});});});
  const first=upload.upload([['M/V/1.jpg',{}]]);
  await upload.upload([['M/V/1.jpg',{}]]);
  assert.equal(calls,1);finish();await first;
});
