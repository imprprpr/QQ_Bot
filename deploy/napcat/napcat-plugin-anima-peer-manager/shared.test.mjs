import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {UserError,atomicWriteJson,conflict,fail,jsonRoute} from './shared.mjs';

function call(handler,req={}){
  return new Promise(resolve=>{let code=200;const res={setHeader(){},status(n){code=n;return this;},json(body){resolve({code,body});}};
    handler({headers:{host:'napcat:6099',...req.headers},body:req.body},res);});
}

test('operator errors pass through, conflicts are 409, internals are logged not shown',async()=>{
  const logged=[];const ctx={logger:{error:(...a)=>logged.push(a)}};
  assert.deepEqual((await call(jsonRoute(ctx,()=>fail('参数无效：每日上限')))).body,{code:400,message:'参数无效：每日上限'});
  assert.equal((await call(jsonRoute(ctx,()=>conflict('已被其他页面更新')))).code,409);
  const hidden=await call(jsonRoute(ctx,()=>{throw new Error("EACCES: /secret/path");},{fallback:'读写失败'}));
  assert.deepEqual(hidden,{code:500,body:{code:500,message:'读写失败'}});
  assert.equal(logged.length,1);assert.match(String(logged[0][1].message),/EACCES/);
  assert.equal((await call(jsonRoute(ctx,async()=>({ok:1})))).body.data.ok,1);
});

test('cross-site and malformed origins are rejected before running the handler',async()=>{
  let ran=false;const handler=jsonRoute({},()=>{ran=true;});
  for(const origin of ['https://evil.example','not a url'])assert.equal((await call(handler,{headers:{origin}})).code,403);
  assert.equal(ran,false);
  assert.equal((await call(handler,{headers:{origin:'http://napcat:6099'}})).code,200);
});

test('atomic write replaces the file, leaves no temp files and keeps the original error',()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'shared-'));const file=path.join(dir,'a.json');
  try{
    atomicWriteJson(file,{v:1});atomicWriteJson(file,{v:2});
    assert.deepEqual(JSON.parse(fs.readFileSync(file,'utf8')),{v:2});
    assert.deepEqual(fs.readdirSync(dir),['a.json']);
    assert.throws(()=>atomicWriteJson(file,{big:'x'.repeat(100)},{maxBytes:10}),UserError);
    assert.throws(()=>atomicWriteJson(path.join(file,'child.json'),{v:3}),e=>!(e instanceof UserError));
    assert.deepEqual(fs.readdirSync(dir),['a.json']);
  }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
