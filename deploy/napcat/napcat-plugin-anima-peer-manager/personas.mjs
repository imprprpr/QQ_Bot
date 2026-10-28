import fs from 'node:fs';
import {randomUUID} from 'node:crypto';
import {atomicWriteJson,conflict,fail,jsonRoute} from './shared.mjs?v=1.8.0';
import {readStatus} from './monitor.mjs?v=1.8.0';

export const textFields={name:80,description:6000,personality:6000,scenario:3000,first_mes:1000,mes_example:8000,system_prompt:6000};
export function normalizeStyle(value){
 const defaults={teasing:1,refusal:1,punctuation:'natural'};
 if(value==null)return defaults;
 if(typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(k=>!Object.hasOwn(defaults,k)))fail('聊天风格无效');
 const d={...defaults,...value};
 if(!Number.isInteger(d.teasing)||d.teasing<0||d.teasing>3||!Number.isInteger(d.refusal)||d.refusal<0||d.refusal>2||!['natural','standard','minimal'].includes(d.punctuation))fail('聊天风格无效');
 return d;
}
export function readPersonas(file){
 if(!fs.existsSync(file))return {schema_version:1,revision:'',profiles:[],bindings:[]};
 if(fs.statSync(file).size>1024*1024)fail('配置过大');
 const d=JSON.parse(fs.readFileSync(file,'utf8'));
 if(d.schema_version!==1||!Array.isArray(d.profiles)||d.profiles.length>50||!Array.isArray(d.bindings)||d.bindings.length>1000)fail('人设配置损坏');
 const ids=new Set();
 for(const p of d.profiles){if(!/^persona-[a-f0-9-]{36}$/.test(p.id)||ids.has(p.id))fail('人设标识无效');ids.add(p.id);validateProfile(p);p.reply_style=normalizeStyle(p.reply_style);}
 const keys=new Set();for(const b of d.bindings){const k=b.bot_id+':'+b.user_id;if(!/^[1-9][0-9]{0,19}$/.test(b.user_id)||!(b.bot_id==='*'||/^[1-9][0-9]{0,19}$/.test(b.bot_id))||typeof b.persona_id!=='string'||!b.persona_id||b.persona_id.length>100||keys.has(k))fail('分配格式无效');keys.add(k);}
 return d;
}
function validateProfile(input){
 const p={base_card:String(input.base_card||''),reply_style:normalizeStyle(input.reply_style)};
 if(p.base_card.length>80||p.base_card.startsWith('persona-'))fail('请选择已有基础角色');
 for(const [key,max] of Object.entries(textFields)){if(typeof input[key]!=='string'||input[key].length>max)fail('人设字段过长或格式不正确');p[key]=input[key];}
 if(!p.name.trim()||!(p.description.trim()||p.personality.trim()))fail('请填写名称及角色描述或性格');
 return p;
}
function write(file,d){d.revision=randomUUID();return atomicWriteJson(file,d,{maxBytes:1024*1024});}
export async function mutatePersonas(file,input,operation,getCatalog){
 const d=readPersonas(file);if(input.revision!==d.revision)conflict('其他页面已更新，请重新读取');
 if(operation==='save'){
  const previous=d.profiles.find(p=>p.id===input.id);if(input.id&&!previous)fail('预设不存在');
  const p={...validateProfile({...input,reply_style:input.reply_style===undefined?previous?.reply_style:input.reply_style}),id:previous?.id||'persona-'+randomUUID()};
  if(previous)d.profiles[d.profiles.indexOf(previous)]=p;else{if(d.profiles.length>=50)fail('最多50个人设');d.profiles.push(p);}
  return {...write(file,d),saved_id:p.id};
 }
 const bot=String(input.bot_id||'*');if(bot!=='*'&&!/^[1-9][0-9]{0,19}$/.test(bot))fail('Bot账号无效');
 const users=[...new Set(String(input.users||'').split(/[\s,，;；]+/).filter(Boolean))];
 if(!users.length||users.length>100||users.some(u=>!/^[1-9][0-9]{0,19}$/.test(u)))fail('请输入QQ号，每行一个，最多100个');
 if(operation==='assign'){
  const id=String(input.persona_id||'');
  if(!d.profiles.some(p=>p.id===id)&&!(await getCatalog()).cards.some(p=>p.id===id))fail('请选择有效的人设');
  for(const user of users){const b=d.bindings.find(b=>b.bot_id===bot&&b.user_id===user);if(b)b.persona_id=id;else d.bindings.push({bot_id:bot,user_id:user,persona_id:id});}
  if(d.bindings.length>1000)fail('最多1000条分配');
 }else if(operation==='unbind')d.bindings=d.bindings.filter(b=>b.bot_id!==bot||!users.includes(b.user_id));
 else fail('未知操作');
 if(readPersonas(file).revision!==input.revision)conflict('配置已更新，请重新读取后再试');
 return write(file,d);
}
export function registerPersonas(ctx){if(!ctx.router)return;const file=ctx.configPath+'.personas.json';
 // Built-in card validation asks the bot; when it is offline, say so instead of a generic failure.
 const catalog=()=>readStatus(ctx.configPath+'.monitor.json',undefined,'personas').catch(()=>fail('内置人设列表需要 Bot 在线，请启动 Bot 后重试',503));
 const handler=fn=>jsonRoute(ctx,fn,{fallback:'人设配置读写失败，详情见 NapCat 日志。'});
 ctx.router.get('/personas/config',handler(()=>readPersonas(file)));
 for(const op of ['save','assign','unbind'])ctx.router.post('/personas/'+op,handler(body=>mutatePersonas(file,body,op,catalog)));
 ctx.router.page({path:'personas',title:'AnimaBot 人设与用户分配',htmlFile:'personas.html',icon:'🎭',description:'保存可复用的人设，按QQ号分配，切换保留独立记忆'});
}
