import fs from 'node:fs';
import https from 'node:https';
import dns from 'node:dns/promises';
import net from 'node:net';
import { randomUUID } from 'node:crypto';
import {UserError,atomicWriteJson,conflict,fail,jsonRoute} from './shared.mjs?v=1.9.0';

// Provider catalog shared with the bot (copied from src/plugins/chat_and_draw/providers.json by build-ui.mjs).
// NapCat only reaches public HTTPS services, so local-model presets are left to the bot's own admin page.
function loadPresets() {
  try {
    const data = JSON.parse(fs.readFileSync(new URL('./providers.json', import.meta.url), 'utf8'));
    return data.providers.filter(p => !p.local).map(({id, name, group, badge, base_url, key_url, note, needs_proxy, fields, model_hints, recommended}) =>
      ({id, name, group, badge, base_url, key_url, note, needs_proxy, fields, model_hints, recommended}));
  } catch {
    return [{id: 'custom', name: '自定义兼容 API', base_url: '', model_hints: []}];
  }
}
export const presets = loadPresets();
const NOT_CHAT = /embed|rerank|whisper|tts|audio|speech|image|dall-e|moderation|ocr|vl-|-vl|vision-exp|transcri/i;
const naturalKey = text => text.toLowerCase().split(/(\d+)/).map(part => /^\d+$/.test(part) ? part.padStart(12, '0') : part).join('');
// Same rules as providers.suggest_model on the bot side: catalog hints against the live list.
export function suggestModel(entry, ids) {
  const chat = ids.filter(id => !NOT_CHAT.test(id));
  const hints = entry?.model_hints || [];
  for (const hint of hints) if (chat.includes(hint)) return hint;
  for (const hint of hints) {
    const matches = chat.filter(id => id.toLowerCase().includes(hint.toLowerCase()) && !/preview|exp|beta/i.test(id));
    if (matches.length) return matches.reduce((a, b) => naturalKey(b) > naturalKey(a) ? b : a);
  }
  return chat.length === 1 ? chat[0] : null;
}
export function baseUrl(value) {
  let url;
  try { url = new URL(String(value)); } catch { fail('请填写完整的 HTTPS API 基址'); }
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash ||
      !url.hostname || net.isIP(url.hostname.replace(/^\[|\]$/g,'')) || url.hostname === 'localhost' ||
      url.hostname.endsWith('.local') || (url.port && url.port !== '443')) fail('API 地址须为公网 HTTPS 域名，不得包含凭据、查询参数或自定义端口');
  const result = url.toString().replace(/\/+$/, '');
  if (/\/(chat\/completions|models)$/.test(url.pathname)) fail('请填写 API 基址，不要附加 /models 或 /chat/completions');
  return result;
}
export function readModels(file) {
  if (!fs.existsSync(file)) return {schema_version:1, revision:'', active:null, profiles:[]};
  if (fs.statSync(file).size > 256*1024) fail('模型配置过大');
  let data;
  try {data=JSON.parse(fs.readFileSync(file,'utf8'));} catch {fail('模型配置无法读取，请从备份恢复');}
  if (data.schema_version !== 1 || !Array.isArray(data.profiles) || data.profiles.length > 50 ||
      (data.active !== null && !data.profiles.some(p => p.id === data.active))) fail('模型配置损坏，请从备份恢复');
  return data;
}
export function publicModels(data) {
  return {revision:data.revision, active:data.active, profiles:data.profiles.map(({api_key,...rest}) => ({...rest,has_key:!!api_key}))};
}
function writeModels(file,data) {
  data.revision=randomUUID();
  atomicWriteJson(file,data);
  fs.chmodSync(file,0o640);
  return publicModels(data);
}
function profileInput(data,input) {
  const previous = data.profiles.find(p => p.id === input.id);
  const base_url=baseUrl(input.base_url);
  const name=String(input.name||'').trim();
  const model=String(input.model||'').trim();
  const api_key=String(input.api_key||'').trim() || (previous?.base_url === base_url ? previous.api_key : '');
  if (!name || name.length>80 || model.length>200 || /[\x00-\x1f]/.test(name+model) || !api_key || api_key.length>4096 || /\s/.test(api_key)) fail('请填写名称、有效 API Key 和模型；更换地址时需要重新输入密钥');
  return {id:previous?.id || randomUUID(),name,provider:String(input.provider||previous?.provider||'custom').slice(0,40),base_url,api_key,model};
}
export function mutateModels(file,input,operation) {
  const data=readModels(file);
  if(input.revision!==data.revision) conflict('配置已在其他页面更新，请重新读取后再试');
  if(operation==='save') {
    const profile=profileInput(data,input);
    if(!profile.model) fail('请先选择或手动填写模型 ID');
    const index=data.profiles.findIndex(p=>p.id===profile.id);
    if(index>=0) data.profiles[index]=profile;
    else {if(data.profiles.length>=50) fail('最多保存50个配置');data.profiles.push(profile);}
    return {...writeModels(file,data),saved_id:profile.id};
  }
  if(operation==='activate') {
    if(input.id!==null && !data.profiles.some(p=>p.id===input.id)) fail('配置不存在');
    data.active=input.id;
  } else if(operation==='delete') {
    if(data.active===input.id) fail('请先切换到其他配置或恢复部署默认，再删除');
    data.profiles=data.profiles.filter(p=>p.id!==input.id);
  } else fail('未知操作');
  return writeModels(file,data);
}
export function publicAddress(address) {
  if(net.isIP(address)===4) {
    const [a,b]=address.split('.').map(Number);
    return !(a===0 || a===10 || a===127 || a>=224 || (a===169&&b===254) || (a===172&&b>=16&&b<=31) || (a===192&&b===168) || (a===100&&b>=64&&b<=127) || (a===198&&(b===18||b===19)));
  }
  // Use global unicast only; reject mapped IPv4, ULA, link-local and loopback.
  return net.isIP(address)===6 && /^[23]/.test(address);
}
export async function listModels(file,input,transport={lookup:dns.lookup,get:https.get}) {
  const profile=profileInput(readModels(file),input);
  const url=new URL(profile.base_url+'/models');
  let dnsTimer;
  const addresses=await Promise.race([transport.lookup(url.hostname,{all:true}),new Promise((_,reject)=>{dnsTimer=setTimeout(()=>reject(new UserError('解析提供商地址超时')),8000);})]).finally(()=>clearTimeout(dnsTimer));
  if(!addresses.length || addresses.some(a=>!publicAddress(a.address))) fail('不允许连接内网或保留地址');
  const chosen=addresses.find(a=>a.family===4)||addresses[0];
  const data=await new Promise((resolve,reject)=>{
    const req=transport.get(url,{headers:{Authorization:'Bearer '+profile.api_key,Accept:'application/json'},
      lookup:(_host,options,callback)=>options?.all ? callback(null,[chosen]) : callback(null,chosen.address,chosen.family)},res=>{
      if(res.statusCode!==200) {res.resume();reject(new UserError('模型列表请求失败，HTTP '+res.statusCode+'；请检查密钥、额度和地址'));return;}
      let size=0;const chunks=[];
      res.on('data',chunk=>{size+=chunk.length;if(size>2*1024*1024) res.destroy(new UserError('列表响应过大'));else chunks.push(chunk);});
      res.on('error',()=>reject(new UserError('读取模型列表失败')));
      res.on('end',()=>{try{resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));}catch{reject(new UserError('提供商未返回有效的模型列表'));}});
    });
    const timer=setTimeout(()=>req.destroy(new Error('MODEL_LIST_TIMEOUT')),15000);
    req.on('close',()=>clearTimeout(timer));
    req.on('error',()=>reject(new UserError('无法连接提供商或请求超时，请检查地址和线路')));
  });
  if(!Array.isArray(data.data)) fail('此接口不支持兼容模型列表，请手动填写模型 ID');
  return [...new Set(data.data.map(p=>p.id).filter(id=>typeof id==='string'&&id.length<=200))].sort().slice(0,2000);
}
export function registerModels(ctx) {
  if(!ctx.router) return;
  const file=ctx.configPath+'.models.json';
  const handler=(fn,fallback='模型配置读写失败，详情见 NapCat 日志。')=>jsonRoute(ctx,fn,{fallback});
  // Use NapCat's authenticated routes, never expose provider secrets as static files.
  ctx.router.get('/models/config',handler(()=>({...publicModels(readModels(file)),presets})));
  for(const op of ['save','activate','delete']) ctx.router.post('/models/'+op,handler(input=>mutateModels(file,input,op)));
  ctx.router.post('/models/list',handler(async input=>{const models=await listModels(file,input);return {models,suggested:suggestModel(presets.find(p=>p.id===input.provider),models)};},'无法连接提供商或请求超时，请检查地址和线路'));
  ctx.router.page({path:'models',title:'AnimaBot 模型提供商',htmlFile:'models.html',icon:'🔌',description:'配置 API、拉取模型与切换聊天模型'});
}
