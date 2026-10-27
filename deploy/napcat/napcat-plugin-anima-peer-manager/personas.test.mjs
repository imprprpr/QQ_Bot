import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {textFields,readPersonas,mutatePersonas,registerPersonas} from './personas.mjs';

test('save reusable presets, edit, assign and unbind with stale revision protection',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'personas-')),file=path.join(dir,'config.json');
 try{let d=readPersonas(file);const input={...Object.fromEntries(Object.keys(textFields).map(k=>[k,''])),name:'角色一',personality:'有趣',base_card:'ling'};
 d=await mutatePersonas(file,{...input,revision:d.revision},'save');const id=d.saved_id;assert.match(id,/^persona-/);
 d=await mutatePersonas(file,{revision:d.revision,persona_id:id,users:'123,456',bot_id:'*'},'assign');assert.equal(d.bindings.length,2);
 d=await mutatePersonas(file,{...input,id,revision:d.revision,name:'更新角色'},'save');assert.equal(d.profiles.length,1);assert.equal(d.bindings[0].persona_id,id);
 await assert.rejects(mutatePersonas(file,{...input,revision:''},'save'));
 d=await mutatePersonas(file,{revision:d.revision,users:'123',bot_id:'*'},'unbind');assert.equal(d.bindings.length,1);assert.equal(d.profiles.length,1);
 assert.equal(readPersonas(file).profiles[0].name,'更新角色');
 }finally{fs.rmSync(dir,{recursive:true});}
});
test('built-in assignments validate catalog, and concurrent writes cannot clobber a preset',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'personas-')),file=path.join(dir,'config.json');
 try{
  await assert.rejects(mutatePersonas(file,{revision:'',users:'123',bot_id:'*',persona_id:'missing'},'assign',async()=>({cards:[]})));
  const d=await mutatePersonas(file,{revision:'',users:'123',bot_id:'999',persona_id:'ling'},'assign',async()=>({cards:[{id:'ling'}]}));
  assert.equal(d.bindings[0].persona_id,'ling');
  let release;
  const pending=mutatePersonas(file,{revision:d.revision,users:'456',bot_id:'999',persona_id:'ling'},'assign',()=>new Promise(resolve=>{release=resolve;}));
  const updated=await mutatePersonas(file,{revision:d.revision,users:'123',bot_id:'999'},'unbind');
  release({cards:[{id:'ling'}]});
  await assert.rejects(pending);
  assert.deepEqual(readPersonas(file),updated);
 }finally{fs.rmSync(dir,{recursive:true});}
});
test('persona routes require NapCat authentication; cross-origin writes rejected',async()=>{
 const routes=[];registerPersonas({configPath:'unused',router:{get:(...a)=>routes.push(a),post:(...a)=>routes.push(a),page(){}}});
 assert.equal(routes.length,4);let code;const res={setHeader(){},status(n){code=n;return this;},json(){}};
 await routes[1][1]({headers:{origin:'https://evil.example',host:'localhost'}},res);assert.equal(code,403);
});

test('style preferences persist separately per preset and older clients cannot reset them',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'persona-style-')),file=path.join(dir,'config.json');
 try{
  const input={...Object.fromEntries(Object.keys(textFields).map(k=>[k,''])),name:'安静角色',personality:'随和',base_card:'ling'};
  let d=await mutatePersonas(file,{...input,revision:'',reply_style:{teasing:0,refusal:0,punctuation:'standard'}},'save');
  const first=d.saved_id;
  d=await mutatePersonas(file,{...input,name:'斗嘴角色',revision:d.revision,reply_style:{teasing:3,refusal:2,punctuation:'minimal'}},'save');
  assert.equal(d.profiles.length,2);
  d=await mutatePersonas(file,{...input,id:first,name:'改名',revision:d.revision},'save');
  assert.deepEqual(d.profiles[0].reply_style,{teasing:0,refusal:0,punctuation:'standard'});
  assert.deepEqual(readPersonas(file).profiles[1].reply_style,{teasing:3,refusal:2,punctuation:'minimal'});
  const before=fs.readFileSync(file,'utf8');
  for(const bad of [{teasing:true},{refusal:3},{punctuation:'bad'},{toString:'override'}]){
   await assert.rejects(mutatePersonas(file,{...input,id:first,revision:d.revision,reply_style:bad},'save'));
   assert.equal(fs.readFileSync(file,'utf8'),before);
  }
  const legacy={schema_version:1,revision:'legacy',profiles:[{...input,id:first}],bindings:[]};
  fs.writeFileSync(file,JSON.stringify(legacy));
  assert.deepEqual(readPersonas(file).profiles[0].reply_style,{teasing:1,refusal:1,punctuation:'natural'});
  assert.equal(JSON.parse(fs.readFileSync(file)).profiles[0].reply_style,undefined);
 }finally{fs.rmSync(dir,{recursive:true});}
});
