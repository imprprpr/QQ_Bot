import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { parseIds, readConfig, saveConfig, plugin_get_config, plugin_set_config } from './index.mjs';

function fixture(fn) {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'anima-peer-'));
  const filename = path.join(folder, 'config.json');
  return Promise.resolve().then(() => fn(filename)).finally(() => fs.rmSync(folder, {recursive: true, force: true}));
}
test('batch separators, deduplication and invalid input', () => {
  assert.deepEqual(parseIds('12345，67890\n12345;23456'), ['12345','23456','67890']);
  for (const invalid of ['all','http://example.com','1=2','123 abc','0001']) assert.throws(() => parseIds(invalid));
});
test('batch append/remove, persistent deletion quarantine and stale revision', () => fixture(filename => {
  const first = saveConfig(filename, {enabled: true, targets: ['12345'], groups: ['67890']});
  const second = saveConfig(filename, {enabled: true, revision: first.revision, targets: first.targets, groups: first.groups,
    addTargets: '23456 34567', removeTargets: '12345', removeGroups: '67890'});
  assert.deepEqual(second.targets, ['23456','34567']);
  assert.deepEqual(second.groups, []);
  assert.ok(second.known_targets.includes('12345'));
  assert.throws(() => saveConfig(filename, {enabled: true, revision: first.revision}), /配置已/);
  assert.deepEqual(readConfig(filename).targets, second.targets);
}));
test('invalid save preserves previous file', () => fixture(filename => {
  saveConfig(filename, {enabled: true, targets: ['12345'], groups: []});
  const before = fs.readFileSync(filename);
  assert.throws(() => saveConfig(filename, {enabled: true, targets: ['@all'], groups: []}));
  assert.deepEqual(fs.readFileSync(filename), before);
}));
test('native NapCat config hooks read QQ lists, no send actions', () => fixture(async filename => {
  const actions = [];
  const ctx = {configPath: filename, adapterName: 'plugin_manager', pluginManager: {config: {}},
    actions: {call: async action => { actions.push(action); return {data: action === 'get_group_list' ? [{group_id:67890,group_name:'群'}] : [{user_id:12345,nickname:'好友'}]}; }}};
  await plugin_set_config(ctx, {enabled: true, targets: [], groups: [], addTargets:'12345', addGroups:'67890'});
  const data = await plugin_get_config(ctx);
  assert.deepEqual(data.targets,['12345']);
  assert.equal(data.addTargets,'');
  assert.ok(actions.every(action => ['get_friend_list','get_group_list'].includes(action)));
}));
