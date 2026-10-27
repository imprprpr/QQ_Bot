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
