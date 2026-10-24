import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {EventEmitter} from 'node:events';
import {Readable} from 'node:stream';
import {baseUrl,readModels,publicModels,mutateModels,publicAddress,registerModels,listModels} from './models.mjs';

function fixture(fn){const dir=fs.mkdtempSync(path.join(os.tmpdir(),'anima-models-'));return Promise.resolve().then(()=>fn(path.join(dir,'models.json'))).finally(()=>fs.rmSync(dir,{recursive:true,force:true}));}
const profile={name:'fixture',base_url:'https://example.com/v1',api_key:'PRIVATE_KEY_FIXTURE',model:'chosen-model'};
test('save, hot activate, default restore, delete and CAS revision',()=>fixture(file=>{
  const saved=mutateModels(file,{...profile,revision:''},'save');
  assert.equal(saved.active,null);assert.equal(saved.profiles[0].has_key,true);
  assert.ok(!JSON.stringify(saved).includes('PRIVATE_KEY_FIXTURE'));
  const active=mutateModels(file,{revision:saved.revision,id:saved.saved_id},'activate');
  assert.equal(readModels(file).active,saved.saved_id);
  assert.throws(()=>mutateModels(file,{revision:saved.revision,id:null},'activate'),/配置已/);
  assert.throws(()=>mutateModels(file,{revision:active.revision,id:saved.saved_id},'delete'),/先切换/);
  const restored=mutateModels(file,{revision:active.revision,id:null},'activate');
  const deleted=mutateModels(file,{revision:restored.revision,id:saved.saved_id},'delete');
  assert.equal(deleted.profiles.length,0);
}));
test('blank key preserves secret only on same origin; invalid save leaves disk untouched',()=>fixture(file=>{
  const saved=mutateModels(file,{...profile,revision:''},'save');
  const updated=mutateModels(file,{...profile,id:saved.saved_id,api_key:'',revision:saved.revision,model:'next'},'save');
  assert.equal(readModels(file).profiles[0].api_key,profile.api_key);
  const before=fs.readFileSync(file);
  assert.throws(()=>mutateModels(file,{...profile,id:saved.saved_id,api_key:'',base_url:'https://other.example/v1',revision:updated.revision},'save'),/密钥/);
  assert.deepEqual(before,fs.readFileSync(file));
}));
test('reject private endpoints, embedded credentials, plaintext and redirects in base',()=>{
  for(const url of ['http://example.com','https://127.0.0.1','https://[::1]','https://user:secret@example.com','https://example.com/?key=secret','https://example.com:1234','https://localhost','https://example.com/v1/models'])assert.throws(()=>baseUrl(url));
  assert.equal(baseUrl('https://example.com/v1/'),'https://example.com/v1');
  for(const address of ['127.0.0.1','169.254.169.254','10.0.0.1','172.16.0.2','192.168.1.1','100.100.100.200','::1','::ffff:127.0.0.1','fc00::1','fe80::1'])assert.equal(publicAddress(address),false,address);
  assert.equal(publicAddress('8.8.8.8'),true);
});
test('routes are authenticated, page registered, secrets never returned',()=>fixture(async file=>{
  const routes=new Map();const pages=[];
  registerModels({configPath:file,router:{get:(p,fn)=>routes.set('GET '+p,fn),post:(p,fn)=>routes.set('POST '+p,fn),page:p=>pages.push(p)}});
  assert.equal(routes.size,5);assert.equal(pages[0].path,'models');
  let result;const response={setHeader(){},status(){return this;},json(data){result=data;}};
  await routes.get('POST /models/save')({body:{...profile,revision:''},headers:{host:'example.com',origin:'https://example.com'}},response);
  assert.equal(result.code,0);assert.ok(!JSON.stringify(result).includes(profile.api_key));
  await routes.get('GET /models/config')({headers:{host:'example.com',origin:'https://evil.example'}},response);
  assert.equal(result.code,403);
}));
test('malformed JSON errors do not echo secret fragments',()=>fixture(file=>{
  fs.writeFileSync(file,'{"api_key":"SECRET_FRAGMENT');
  assert.throws(()=>readModels(file),error=>!error.message.includes('SECRET_FRAGMENT'));
}));
test('model discovery sends key only to chosen endpoint, sorts models and rejects private DNS',()=>fixture(async file=>{
  let calls=0;
  const transport={lookup:async()=>[{address:'8.8.8.8',family:4}],get:(url,options,callback)=>{
    calls++;assert.equal(url.pathname,'/v1/models');assert.equal(options.headers.Authorization,'Bearer '+profile.api_key);
    const req=new EventEmitter();req.destroy=()=>req.emit('error',new Error('closed'));
    queueMicrotask(()=>{const res=Readable.from([Buffer.from(JSON.stringify({data:[{id:'z'},{id:'a'},{id:'a'}]}))]);res.statusCode=200;callback(res);res.on('end',()=>req.emit('close'));});return req;
  }};
  assert.deepEqual(await listModels(file,profile,transport),['a','z']);
  await assert.rejects(()=>listModels(file,profile,{...transport,lookup:async()=>[{address:'169.254.169.254',family:4}]}),/保留地址/);
  assert.equal(calls,1);
}));
