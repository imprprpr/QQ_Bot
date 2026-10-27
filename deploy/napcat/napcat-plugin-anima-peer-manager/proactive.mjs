import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';

export const defaults = {schema_version:1, revision:'', enabled:true, utc_offset:8,
  windows:[[480,600],[720,840],[1140,1320]], idle_hours:3, cooldown_hours:6,
  daily_limit:2, global_daily_limit:6};

export function validateProactive(input) {
  if (!input || input.schema_version!==1 || typeof input.enabled!=='boolean') throw Error('配置格式不正确');
  for (const [key,min,max] of [['utc_offset',-12,14],['idle_hours',1,24],['cooldown_hours',3,48],['daily_limit',0,3],['global_daily_limit',0,20]]) {
    const n=input[key];
    if (typeof n!=='number' || !Number.isFinite(n) || n<min || n>max || (key.endsWith('limit')&&!Number.isInteger(n))) throw Error('参数超出允许范围：'+key);
  }
  if (!Array.isArray(input.windows) || input.windows.length<1 || input.windows.length>8 ||
      input.windows.some(w=>!Array.isArray(w)||w.length!==2||w.some(n=>!Number.isInteger(n)))) throw Error('请填写1至8个有效时间段');
  const windows=input.windows.map(w=>[...w]).sort((a,b)=>a[0]-b[0]);
  let end=-1;
  for (const [start,stop] of windows) {
    if(start<0 || start>=stop || stop>1440 || start<end) throw Error('时间段不得重叠或跨午夜，跨午夜请拆成两段');
    end=stop;
  }
  return Object.fromEntries(Object.keys(defaults).map(k=>[k,k==='windows'?windows:input[k]]));
}
export function readProactive(file) {
  if(!fs.existsSync(file)) return structuredClone(defaults);
  if(fs.statSync(file).size>32768) throw Error('主动聊天配置过大');
  return validateProactive(JSON.parse(fs.readFileSync(file,'utf8')));
}
export function saveProactive(file,input) {
  const old=readProactive(file);
  if(input.revision!==old.revision) throw Error('配置已被其他页面更新，请重新读取后保存');
  const data={...validateProactive(input),revision:randomUUID()};
  fs.mkdirSync(path.dirname(file),{recursive:true});
  const temp=file+'.'+randomUUID()+'.tmp';
  try {
    const fd=fs.openSync(temp,'wx',0o640);
    try {fs.writeFileSync(fd,JSON.stringify(data,null,2)+'\n');fs.fsyncSync(fd);} finally {fs.closeSync(fd);}
    fs.renameSync(temp,file);
  } finally {if(fs.existsSync(temp))fs.unlinkSync(temp);}
  return data;
}
export function registerProactive(ctx) {
  if(!ctx.router)return;
  const file=ctx.configPath+'.proactive.json';
  const handler=fn=>(req,res)=>{
    res.setHeader('Cache-Control','no-store');
    try {
      if(req.headers?.origin && new URL(req.headers.origin).host!==req.headers.host) {
        res.status(403).json({code:403,message:'跨站请求被拒绝'});return;
      }
      res.json({code:0,data:fn(req.body||{})});
    } catch {res.status(400).json({code:400,message:'保存或读取失败：请检查数值范围、时间段是否重叠；若配置已更新，请重新读取。'});}
  };
  // NapCat authenticated routes only. No action here can send a QQ message.
  ctx.router.get('/proactive/config',handler(()=>readProactive(file)));
  ctx.router.post('/proactive/config',handler(input=>saveProactive(file,input)));
  ctx.router.page({path:'proactive',title:'AnimaBot 主动聊天',htmlFile:'proactive.html',icon:'💬',description:'开关、时间段、冷却与每日额度，保存后自动生效'});
}
