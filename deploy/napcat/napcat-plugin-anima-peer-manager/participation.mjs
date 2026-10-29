import fs from 'node:fs';
import {randomUUID} from 'node:crypto';
import {atomicWriteJson,conflict,fail,jsonRoute} from './shared.mjs?v=1.9.0';

export const fields=JSON.parse(fs.readFileSync(new URL('./participation.schema.json',import.meta.url),'utf8'));
export const defaults=()=>({schema_version:2,revision:'',values:Object.fromEntries(fields.map(f=>[f.key,f.default]))});
const legacyFields=JSON.parse(fs.readFileSync(new URL('./participation.v1.schema.json',import.meta.url),'utf8'));
export function validateParticipation(data) {
  if(data?.schema_version===1){
    const v=data.values;
    if(!v||Object.keys(v).length!==legacyFields.length||legacyFields.some(f=>f.type==='boolean'?typeof v[f.key]!=='boolean':typeof v[f.key]!=='number'||!Number.isFinite(v[f.key])||v[f.key]<f.min||v[f.key]>f.max||(f.integer&&!Number.isInteger(v[f.key]))))fail('旧配置格式无效');
    const values=Object.fromEntries(fields.map(f=>[f.key,v[f.key]??f.default]));
    values.GROUP_CHAT_AMBIENT_GLOBAL_HOURLY_LIMIT=v.GROUP_CHAT_GLOBAL_HOURLY_LIMIT;
    values.GROUP_CHAT_AMBIENT_GLOBAL_DAILY_LIMIT=v.GROUP_CHAT_GLOBAL_DAILY_LIMIT;
    values.GROUP_CHAT_AMBIENT_HOURLY_LIMIT=Math.min(v.GROUP_CHAT_AMBIENT_HOURLY_LIMIT,v.GROUP_CHAT_HOURLY_LIMIT);
    values.GROUP_CHAT_AMBIENT_DAILY_LIMIT=Math.min(v.GROUP_CHAT_AMBIENT_DAILY_LIMIT,v.GROUP_CHAT_DAILY_LIMIT);
    for(const f of fields)if(f.type==='number')values[f.key]=Math.max(f.min,Math.min(f.max,values[f.key]));
    return validateParticipation({...data,schema_version:2,values});
  }
  if(!data || data.schema_version!==2 || !data.values || Array.isArray(data.values) ||
      Object.keys(data.values).length!==fields.length) fail('配置格式不正确');
  for(const f of fields) {
    const v=data.values[f.key];
    if(f.type==='boolean' ? typeof v!=='boolean' :
        typeof v!=='number'||!Number.isFinite(v)||v<f.min||v>f.max||(f.integer&&!Number.isInteger(v))) fail('参数无效：'+f.label);
  }
  return {schema_version:2,revision:data.revision,values:Object.fromEntries(fields.map(f=>[f.key,data.values[f.key]]))};
}
export function readParticipation(file) {
  if(!fs.existsSync(file))return defaults();
  if(fs.statSync(file).size>32768)fail('配置过大');
  return validateParticipation(JSON.parse(fs.readFileSync(file,'utf8')));
}
export function saveParticipation(file,input) {
  const old=readParticipation(file);
  if(input.revision!==old.revision)conflict('其他页面已更新配置，请重新读取后保存');
  return atomicWriteJson(file,{...validateParticipation(input),revision:randomUUID()},{maxBytes:32768});
}
export function registerParticipation(ctx) {
  if(!ctx.router)return;
  const file=ctx.configPath+'.participation.json';
  const handle=fn=>jsonRoute(ctx,fn,{fallback:'群聊配置读写失败，详情见 NapCat 日志。'});
  ctx.router.get('/participation/config',handle(()=>({...readParticipation(file),fields})));
  ctx.router.post('/participation/config',handle(body=>({...saveParticipation(file,body),fields})));
  ctx.router.page({path:'participation',title:'AnimaBot 群聊与互聊',htmlFile:'participation.html',icon:'👥',description:'群聊参与、bot互聊、冷却、轮次与额度热更新'});
}
