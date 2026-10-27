import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {readStatus} from './monitor.mjs?v=1.7.0';

export const textFields={name:80,description:6000,personality:6000,scenario:3000,first_mes:1000,mes_example:8000,system_prompt:6000};
export function normalizeStyle(value){
 const defaults={teasing:1,refusal:1,punctuation:'natural'};
 if(value==null)return defaults;
 if(typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(k=>!Object.hasOwn(defaults,k)))throw Error('聊天风格无效');
 const d={...defaults,...value};
 if(!Number.isInteger(d.teasing)||d.teasing<0||d.teasing>3||!Number.isInteger(d.refusal)||d.refusal<0||d.refusal>2||!['natural','standard','minimal'].includes(d.punctuation))throw Error('聊天风格无效');
 return d;
}
export function readPersonas(file){
 if(!fs.existsSync(file))return {schema_version:1,revision:'',profiles:[],bindings:[]};
 if(fs.statSync(file).size>1024*1024)throw Error('配置过大');
 const d=JSON.parse(fs.readFileSync(file,'utf8'));
 if(d.schema_version!==1||!Array.isArray(d.profiles)||d.profiles.length>50||!Array.isArray(d.bindings)||d.bindings.length>1000)throw Error('人设配置损坏');
 const ids=new Set();
 for(const p of d.profiles){if(!/^persona-[a-f0-9-]{36}$/.test(p.id)||ids.has(p.id))throw Error('人设标识无效');ids.add(p.id);validateProfile(p);p.reply_style=normalizeStyle(p.reply_style);}
 const keys=new Set();for(const b of d.bindings){const k=b.bot_id+':'+b.user_id;if(!/^[1-9][0-9]{0,19}$/.test(b.user_id)||!(b.bot_id==='*'||/^[1-9][0-9]{0,19}$/.test(b.bot_id))||typeof b.persona_id!=='string'||!b.persona_id||b.persona_id.length>100||keys.has(k))throw Error('分配格式无效');keys.add(k);}
 return d;
}
function validateProfile(input){
 const p={base_card:String(input.base_card||''),reply_style:normalizeStyle(input.reply_style)};
 if(p.base_card.length>80||p.base_card.startsWith('persona-'))throw Error('请选择已有基础角色');
 for(const [key,max] of Object.entries(textFields)){if(typeof input[key]!=='string'||input[key].length>max)throw Error('人设字段过长或格式不正确');p[key]=input[key];}
 if(!p.name.trim()||!(p.description.trim()||p.personality.trim()))throw Error('请填写名称及角色描述或性格');
 return p;
}
function write(file,d){d.revision=randomUUID();const content=JSON.stringify(d,null,2)+'\n';if(Buffer.byteLength(content)>1024*1024)throw Error('配置过大');fs.mkdirSync(path.dirname(file),{recursive:true});const temp=file+'.'+randomUUID()+'.tmp';try{const fd=fs.openSync(temp,'wx',0o640);try{fs.writeFileSync(fd,content);fs.fsyncSync(fd);}finally{fs.closeSync(fd);}fs.renameSync(temp,file);}finally{if(fs.existsSync(temp))fs.unlinkSync(temp);}return d;}
export async function mutatePersonas(file,input,operation,getCatalog){
 const d=readPersonas(file);if(input.revision!==d.revision)throw Error('其他页面已更新，请重新读取');
 if(operation==='save'){
  const previous=d.profiles.find(p=>p.id===input.id);if(input.id&&!previous)throw Error('预设不存在');
  const p={...validateProfile({...input,reply_style:input.reply_style===undefined?previous?.reply_style:input.reply_style}),id:previous?.id||'persona-'+randomUUID()};
  if(previous)d.profiles[d.profiles.indexOf(previous)]=p;else{if(d.profiles.length>=50)throw Error('最多50个人设');d.profiles.push(p);}
  return {...write(file,d),saved_id:p.id};
 }
 const bot=String(input.bot_id||'*');if(bot!=='*'&&!/^[1-9][0-9]{0,19}$/.test(bot))throw Error('Bot账号无效');
 const users=[...new Set(String(input.users||'').split(/[\s,，;；]+/).filter(Boolean))];
 if(!users.length||users.length>100||users.some(u=>!/^[1-9][0-9]{0,19}$/.test(u)))throw Error('请输入QQ号，每行一个，最多100个');
 if(operation==='assign'){
  const id=String(input.persona_id||'');
  if(!d.profiles.some(p=>p.id===id)&&!(await getCatalog()).cards.some(p=>p.id===id))throw Error('请选择有效的人设');
  for(const user of users){const b=d.bindings.find(b=>b.bot_id===bot&&b.user_id===user);if(b)b.persona_id=id;else d.bindings.push({bot_id:bot,user_id:user,persona_id:id});}
  if(d.bindings.length>1000)throw Error('最多1000条分配');
 }else if(operation==='unbind')d.bindings=d.bindings.filter(b=>b.bot_id!==bot||!users.includes(b.user_id));
 else throw Error('未知操作');
 if(readPersonas(file).revision!==input.revision)throw Error('配置已更新，请重试');
 return write(file,d);
}
export function registerPersonas(ctx){if(!ctx.router)return;const file=ctx.configPath+'.personas.json';const handler=fn=>async(req,res)=>{res.setHeader('Cache-Control','no-store');try{if(req.headers?.origin&&new URL(req.headers.origin).host!==req.headers.host){res.status(403).json({code:403,message:'跨站请求被拒绝'});return;}res.json({code:0,data:await fn(req.body||{})});}catch{res.status(400).json({code:400,message:'操作失败：请检查名称、内容和QQ号；配置变更冲突时重新读取，内置人设列表需Bot在线。'});}};
 ctx.router.get('/personas/config',handler(()=>readPersonas(file)));
 for(const op of ['save','assign','unbind'])ctx.router.post('/personas/'+op,handler(body=>mutatePersonas(file,body,op,()=>readStatus(ctx.configPath+'.monitor.json',undefined,'personas'))));
 ctx.router.page({path:'personas',title:'AnimaBot 人设与用户分配',htmlFile:'personas.html',icon:'🎭',description:'保存可复用的人设，按QQ号分配，切换保留独立记忆'});
}
