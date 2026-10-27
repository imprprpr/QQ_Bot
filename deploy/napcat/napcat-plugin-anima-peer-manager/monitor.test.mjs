import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {EventEmitter} from 'node:events';
import {readStatus,registerMonitor} from './monitor.mjs';

test('fixed loopback read-only route, token stays server-side',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'status-')),file=path.join(dir,'bridge.json');
 const token='PRIVATE_TOKEN_NOT_FOR_BROWSER_123456789';
 fs.writeFileSync(file,JSON.stringify({token,port:8080}));
 const payload={schema_version:1,server_time:123,quotas:{},rooms:[],events:[]};
 try{
  const transport=(options,callback)=>{
   assert.equal(options.hostname,'127.0.0.1');assert.equal(options.path,'/manage/api/participation');
   assert.equal(options.headers.Authorization,'Bearer '+token);
   const req=new EventEmitter();req.destroy=()=>req.emit('error',Error('timeout'));
   queueMicrotask(()=>{const res=new EventEmitter();res.statusCode=200;callback(res);res.emit('data',Buffer.from(JSON.stringify(payload)));res.emit('end');req.emit('close');});
   return req;
  };
  const value=await readStatus(file,transport);assert.deepEqual(value,payload);
  assert.equal(JSON.stringify(value).includes(token),false);
  fs.writeFileSync(file,JSON.stringify({token,port:0}));
  await assert.rejects(readStatus(file,transport));
 }finally{fs.rmSync(dir,{recursive:true});}
});
test('authenticated route only and error messages hide private bridge data',async()=>{
 const routes=[];registerMonitor({configPath:'/missing/private',router:{get:(...a)=>routes.push(a)}});
 assert.equal(routes.length,3);assert.equal(routes[0][0],'/participation/status');
 let code,body;const res={setHeader(){},status(n){code=n;return this;},json(v){body=v;}};
 await routes[0][1]({headers:{origin:'https://evil.example',host:'localhost'}},res);
 assert.equal(code,403);
 await routes[0][1]({headers:{}},res);assert.equal(code,503);
 assert.equal(JSON.stringify(body).includes('/missing/private'),false);
});
