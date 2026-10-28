import fs from 'node:fs';
import {randomUUID} from 'node:crypto';
import {atomicWriteJson,conflict,fail,jsonRoute} from './shared.mjs?v=1.8.0';

export const defaults = {schema_version:1, revision:'', enabled:true, utc_offset:8,
  windows:[[480,600],[720,840],[1140,1320]], idle_hours:3, cooldown_hours:6,
  daily_limit:2, global_daily_limit:6};

export function validateProactive(input) {
  if (!input || input.schema_version!==1 || typeof input.enabled!=='boolean') fail('配置格式不正确');
  const labels={utc_offset:'时区',idle_hours:'闲置小时',cooldown_hours:'冷却小时',daily_limit:'每人每日上限',global_daily_limit:'全局每日上限'};
  for (const [key,min,max] of [['utc_offset',-12,14],['idle_hours',1,24],['cooldown_hours',3,48],['daily_limit',0,3],['global_daily_limit',0,20]]) {
    const n=input[key];
    if (typeof n!=='number' || !Number.isFinite(n) || n<min || n>max || (key.endsWith('limit')&&!Number.isInteger(n))) fail(`${labels[key]}超出允许范围（${min}–${max}${key.endsWith('limit')?'，需为整数':''}）`);
  }
  if (!Array.isArray(input.windows) || input.windows.length<1 || input.windows.length>8 ||
      input.windows.some(w=>!Array.isArray(w)||w.length!==2||w.some(n=>!Number.isInteger(n)))) fail('请填写1至8个有效时间段');
  const windows=input.windows.map(w=>[...w]).sort((a,b)=>a[0]-b[0]);
  let end=-1;
  for (const [start,stop] of windows) {
    if(start<0 || start>=stop || stop>1440 || start<end) fail('时间段不得重叠或跨午夜，跨午夜请拆成两段');
    end=stop;
  }
  return Object.fromEntries(Object.keys(defaults).map(k=>[k,k==='windows'?windows:input[k]]));
}
export function readProactive(file) {
  if(!fs.existsSync(file)) return structuredClone(defaults);
  if(fs.statSync(file).size>32768) fail('主动聊天配置过大');
  return validateProactive(JSON.parse(fs.readFileSync(file,'utf8')));
}
export function saveProactive(file,input) {
  const old=readProactive(file);
  if(input.revision!==old.revision) conflict('配置已被其他页面更新，请重新读取后保存');
  return atomicWriteJson(file,{...validateProactive(input),revision:randomUUID()},{maxBytes:32768});
}
export function registerProactive(ctx) {
  if(!ctx.router)return;
  const file=ctx.configPath+'.proactive.json';
  const handler=fn=>jsonRoute(ctx,fn,{fallback:'主动聊天配置读写失败，详情见 NapCat 日志。'});
  // NapCat authenticated routes only. No action here can send a QQ message.
  ctx.router.get('/proactive/config',handler(()=>readProactive(file)));
  ctx.router.post('/proactive/config',handler(input=>saveProactive(file,input)));
  ctx.router.page({path:'proactive',title:'AnimaBot 主动聊天',htmlFile:'proactive.html',icon:'💬',description:'开关、时间段、冷却与每日额度，保存后自动生效'});
}
