import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {catalog, pages, render} from './build-ui.mjs';
import {presets, suggestModel} from './models.mjs';

test('every admin page carries the current shared ui (run `node build-ui.mjs` after editing ui/)',()=>{
  for(const page of pages){const html=fs.readFileSync(page,'utf8');assert.equal(render(html),html,page);}
});

test('pages never inject HTML strings and every form control has an accessible name',()=>{
  for(const page of pages){
    const html=fs.readFileSync(page,'utf8');
    assert.doesNotMatch(html,/\.innerHTML|outerHTML\s*=|insertAdjacentHTML|document\.write/,page);
    const labelled=new Set([...html.matchAll(/<label[^>]*for="([^"]+)"/g)].map(m=>m[1]));
    for(const [tag,id] of [...html.matchAll(/<(input|select|textarea)\b[^>]*\bid="([^"]+)"[^>]*>/g)].map(m=>[m[0],m[2]])){
      const wrapped=new RegExp(`<label[^>]*>[^<]*(?:<[^l][^>]*>[^<]*)*<(?:input|select|textarea)[^>]*id="${id}"`).test(html);
      assert.ok(labelled.has(id)||wrapped||/aria-label=/.test(tag)||/type="(?:hidden|search)"/.test(tag)&&labelled.has(id),`${page}: #${id} has no label`);
    }
  }
});

test('plugin ships the current provider catalog and suggests chat models from live lists',()=>{
  assert.equal(fs.readFileSync(catalog.copy,'utf8'),fs.readFileSync(catalog.source,'utf8'),'run `node build-ui.mjs`');
  assert.ok(presets.length>5&&presets.every(p=>!p.base_url.startsWith('http://')),'NapCat presets are remote HTTPS only');
  const deepseek=presets.find(p=>p.id==='deepseek');
  assert.equal(suggestModel(deepseek,['deepseek-v4-pro','deepseek-flash']),'deepseek-flash');
  const gemini=presets.find(p=>p.id==='gemini');
  assert.equal(suggestModel(gemini,['text-embedding-004','gemini-3.6-flash','gemini-3.8-flash','gemini-3.9-flash-preview']),'gemini-3.8-flash');
  assert.equal(suggestModel({model_hints:[]},['only-one']),'only-one');
  assert.equal(suggestModel({model_hints:[]},['a','b']),null);
});
