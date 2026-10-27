import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { registerModels } from './models.mjs?v=1.2.0';

import { registerProactive } from './proactive.mjs?v=1.2.0';

const MAX_IDS = 1000;
export function parseIds(value) {
  const input = Array.isArray(value) ? value : String(value ?? '').split(/[\s,，;；]+/u);
  const values = [...new Set(input.map(item => String(item).trim()).filter(Boolean))];
  if (values.length > MAX_IDS || values.some(item => !/^[1-9][0-9]{0,19}$/.test(item))) {
    throw new Error('号码必须为1至20位纯数字，每个列表最多1000项；支持逗号、空格或换行分隔。');
  }
  return values.sort((a, b) => a.length - b.length || a.localeCompare(b));
}

export function readConfig(filename) {
  if (!fs.existsSync(filename)) return {schema_version: 1, revision: '', enabled: true, targets: [], groups: [], known_targets: []};
  if (fs.statSync(filename).size > 256 * 1024) throw new Error('配置文件过大');
  const data = JSON.parse(fs.readFileSync(filename, 'utf8'));
  if (data.schema_version !== 1 || typeof data.enabled !== 'boolean') throw new Error('配置格式不兼容');
  return {...data, targets: parseIds(data.targets), groups: parseIds(data.groups),
    known_targets: parseIds(data.known_targets ?? data.targets)};
}

export function saveConfig(filename, incoming) {
  const old = readConfig(filename);
  if (typeof incoming.enabled !== 'boolean') throw new Error('启用状态必须为开关值');
  if (incoming.revision !== undefined && incoming.revision !== old.revision) throw new Error('配置已在其他页面更新，请重新打开配置后保存。');
  const merge = (selected, add, remove) => {
    const removing = new Set(parseIds(remove));
    return parseIds([...parseIds(selected), ...parseIds(add)]).filter(id => !removing.has(id));
  };
  const targets = merge(incoming.targets, incoming.addTargets, incoming.removeTargets);
  const groups = merge(incoming.groups, incoming.addGroups, incoming.removeGroups);
  const data = {schema_version: 1, revision: randomUUID(), enabled: incoming.enabled,
    targets, groups, known_targets: parseIds([...old.known_targets, ...old.targets, ...targets]),
    updated_at: new Date().toISOString()};
  // A separate quarantine list still identifies removed bots if the main file
  // is accidentally damaged before AnimaBot restarts. It cannot enable sends.
  atomicWrite(filename + '.known.json', {known_targets: data.known_targets});
  atomicWrite(filename, data);
  return data;
}

function atomicWrite(filename, data) {
  const content = JSON.stringify(data, null, 2) + '\n';
  if (Buffer.byteLength(content) > 256 * 1024) throw new Error('配置文件过大');
  fs.mkdirSync(path.dirname(filename), {recursive: true});
  const temporary = filename + '.' + randomUUID() + '.tmp';
  try {
    const fd = fs.openSync(temporary, 'wx', 0o644);
    try { fs.writeFileSync(fd, content); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
    fs.renameSync(temporary, filename);
  } finally { if (fs.existsSync(temporary)) fs.unlinkSync(temporary); }
}

const labelIds = (list, current, key, label) => {
  const values = new Map(list.map(item => [String(item[key]), String(item[label] || item.remark || item.nickname || item[key])]));
  for (const id of current) if (!values.has(id)) values.set(id, '手动添加');
  return [...values].map(([value, name]) => ({label: `${name} (${value})`, value}));
};
function schema(data, friends = [], groups = []) {
  return [
    {key: 'enabled', type: 'boolean', label: '启用指定机器人互动', default: true, description: '关闭后保留列表；冷却、轮次、额度仍由机器人控制。'},
    {key: 'targets', type: 'multi-select', label: '允许互动的QQ号（可多选）', default: [], options: labelIds(friends, data.targets, 'user_id', 'remark')},
    {key: 'groups', type: 'multi-select', label: '允许互动的群（可多选）', default: [], options: labelIds(groups, data.groups, 'group_id', 'group_name')},
    {key: 'addTargets', type: 'text', label: '批量追加QQ号', default: '', description: '每行一个或用逗号分隔；可追加非好友的机器人QQ号。'},
    {key: 'addGroups', type: 'text', label: '批量追加群号', default: '', description: '每行一个或用逗号分隔。'},
    {key: 'removeTargets', type: 'text', label: '批量移除QQ号', default: '', description: '移除优先于追加；也可在上方取消勾选。'},
    {key: 'removeGroups', type: 'text', label: '批量移除群号', default: ''},
    {key: 'revision', type: 'string', label: '配置版本', hidden: true, default: data.revision},
  ];
}
export let plugin_config_ui = schema({targets: [], groups: [], revision: ''});

async function contacts(ctx, action) {
  if (!ctx.actions?.call) return [];
  let timer;
  try {
    const result = await Promise.race([
      ctx.actions.call(action, {}, ctx.adapterName, ctx.pluginManager.config),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('CONTACT_TIMEOUT')), 4000); })
    ]);
    return Array.isArray(result) ? result : Array.isArray(result?.data) ? result.data : [];
  } catch { return []; } finally { clearTimeout(timer); }
}
export async function plugin_get_config(ctx) {
  const data = readConfig(ctx.configPath);
  const [friends, groups] = await Promise.all([contacts(ctx, 'get_friend_list'), contacts(ctx, 'get_group_list')]);
  plugin_config_ui = schema(data, friends, groups);
  return {enabled: data.enabled, targets: data.targets, groups: data.groups, revision: data.revision,
    addTargets: '', addGroups: '', removeTargets: '', removeGroups: ''};
}
export async function plugin_set_config(ctx, config) {
  saveConfig(ctx.configPath, config);
  await plugin_get_config(ctx);
}
export async function plugin_init(ctx) {
  registerModels(ctx);
  registerProactive(ctx);
  // Read-only until the operator saves in NapCat; never sends QQ messages.
  await plugin_get_config(ctx);
  ctx.logger.info('AnimaBot 群号与QQ管理已加载；请在插件配置中批量编辑并保存。');
}
