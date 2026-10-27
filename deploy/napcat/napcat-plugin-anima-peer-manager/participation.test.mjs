import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {defaults,fields,validateParticipation,readParticipation,saveParticipation,registerParticipation} from './participation.mjs';
test('policy roundtrip, all fields and stale writes',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'participation-')),file=path.join(dir,'policy.json');
 try{
  const first=readParticipation(file);assert.equal(Object.keys(first.values).length,fields.length);
  const data=saveParticipation(file,{...first,values:{...first.values,GROUP_CHAT_ENABLED:false,BOT_INTERACTION_MAX_ROUNDS:1}});
  assert.deepEqual(readParticipation(file),data);assert.throws(()=>saveParticipation(file,first));
  assert.deepEqual(fs.readdirSync(dir),['policy.json']);
  assert.equal(saveParticipation(file,{...data,values:{...data.values,GROUP_CHAT_ENABLED:true}}).values.GROUP_CHAT_ENABLED,true);
 }finally{fs.rmSync(dir,{recursive:true});}
});
test('reject invalid fields, values and excessive bot rounds',()=>{
 for(const values of [{BOT_INTERACTION_MAX_ROUNDS:4},{GROUP_CHAT_ENABLED:1},{GROUP_CHAT_DAILY_LIMIT:-1},{BOT_INTERACTION_DAILY_LIMIT:1.5},{GROUP_CHAT_PROBABILITY:NaN}]){
  const d=defaults();assert.throws(()=>validateParticipation({...d,values:{...d.values,...values}}));
 }
 assert.throws(()=>validateParticipation({schema_version:1,values:{}}));
});
test('authenticated routes only; cross-origin writes rejected',()=>{
 const gets=[],posts=[],pages=[];
 registerParticipation({configPath:'unused',router:{get:(...args)=>gets.push(args),post:(...args)=>posts.push(args),page:p=>pages.push(p)}});
 assert.equal(gets[0][0],'/participation/config');assert.equal(pages[0].path,'participation');
 let code;const res={setHeader(){},status(n){code=n;return this;},json(){}};
 posts[0][1]({headers:{origin:'https://evil.example',host:'localhost'}},res);assert.equal(code,403);
});
