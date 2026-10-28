import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {pages, render} from './build-ui.mjs';

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
