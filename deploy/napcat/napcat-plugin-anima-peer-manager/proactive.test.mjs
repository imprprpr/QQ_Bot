import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {defaults,readProactive,saveProactive,validateProactive,registerProactive} from './proactive.mjs';
test('atomic save, hot read and stale-page rejection',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'proactive-')),file=path.join(dir,'config.json');
 try {
  const first=readProactive(file);assert.equal(first.enabled,true);
  const saved=saveProactive(file,{...first,enabled:false,windows:[[1380,1440],[0,60]],daily_limit:1});
  assert.equal(readProactive(file).enabled,false);assert.deepEqual(saved.windows,[[0,60],[1380,1440]]);
  assert.throws(()=>saveProactive(file,first),/已被其他页面/);
  assert.equal(saveProactive(file,{...saved,enabled:true}).enabled,true);
  assert.deepEqual(fs.readdirSync(dir),['config.json']);
 }finally{fs.rmSync(dir,{recursive:true});}
});
test('reject bad types, overlap, limits and broken file',()=>{
 for(const change of [{enabled:'true'},{daily_limit:4},{global_daily_limit:21},{cooldown_hours:2},{idle_hours:0},{utc_offset:15},{windows:[]},{windows:[[1380,60]]},{windows:[[480,600],[599,700]]},{windows:[null]},{daily_limit:1.5},{idle_hours:NaN}]){
  assert.throws(()=>validateProactive({...defaults,...change}));
 }
});
test('register only authenticated routes; reject cross-origin writes',()=>{
 const gets=[],posts=[],pages=[];registerProactive({configPath:'unused',router:{get:(...a)=>gets.push(a),post:(...a)=>posts.push(a),page:p=>pages.push(p)}});
 assert.deepEqual(gets.map(a=>a[0]),['/proactive/config']);assert.equal(posts.length,1);assert.equal(pages[0].path,'proactive');
 let status,body;const res={setHeader(){},status(n){status=n;return this;},json(v){body=v;}};
 posts[0][1]({headers:{origin:'https://evil.example',host:'localhost'},body:{}},res);
 assert.equal(status,403);assert.equal(body.code,403);
});
