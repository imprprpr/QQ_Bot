import fs from 'node:fs';
import http from 'node:http';

export async function readStatus(file,transport=http.get,kind='participation') {
  const routes={participation:'/manage/api/participation',proactive:'/manage/api/proactive',personas:'/manage/api/persona-catalog'};
  if(!routes[kind])throw Error('INVALID_STATUS_KIND');
  if(fs.statSync(file).size>8192)throw Error('BRIDGE_CONFIG');
  const config=JSON.parse(fs.readFileSync(file,'utf8'));
  if(typeof config.token!=='string'||config.token.length<32||config.token.length>4096||/[^\x21-\x7e]/.test(config.token)||
      !Number.isInteger(config.port)||config.port<1||config.port>65535)throw Error('BRIDGE_CONFIG');
  return new Promise((resolve,reject)=>{
    // Fixed loopback host and read-only route. Credentials never reach the browser.
    const req=transport({hostname:'127.0.0.1',port:config.port,path:routes[kind],
      headers:{Authorization:'Bearer '+config.token,Accept:'application/json'}},res=>{
      if(res.statusCode!==200){res.resume();reject(Error('BOT_UNAVAILABLE'));return;}
      const chunks=[];let size=0;
      res.on('data',chunk=>{size+=chunk.length;if(size>2*1024*1024)res.destroy(Error('STATUS_TOO_LARGE'));else chunks.push(chunk);});
      res.on('error',()=>reject(Error('BOT_UNAVAILABLE')));
      res.on('end',()=>{try{const data=JSON.parse(Buffer.concat(chunks).toString('utf8'));
        if(data.schema_version!==1)throw Error();
        if(kind==='participation'&&(!Number.isFinite(data.server_time)||!Array.isArray(data.rooms)||!Array.isArray(data.events)||!data.quotas))throw Error();
        if(kind==='proactive'&&(!Number.isFinite(data.server_time)||!Array.isArray(data.sessions)))throw Error();
        if(kind==='personas'&&(!Array.isArray(data.cards)||!Array.isArray(data.bot_ids)))throw Error();
        resolve(data);
      }catch{reject(Error('INVALID_STATUS'));}});
    });
    const timer=setTimeout(()=>req.destroy(Error('TIMEOUT')),5000);
    req.on('close',()=>clearTimeout(timer));
    req.on('error',()=>reject(Error('BOT_UNAVAILABLE')));
  });
}

export function registerMonitor(ctx) {
  if(!ctx.router)return;
  for(const [route,kind] of [['/participation/status','participation'],['/proactive/status','proactive'],['/personas/catalog','personas']])ctx.router.get(route,async(req,res)=>{
    res.setHeader('Cache-Control','no-store');
    try{
      if(req.headers?.origin&&new URL(req.headers.origin).host!==req.headers.host){res.status(403).json({code:403,message:'跨站请求被拒绝'});return;}
      res.json({code:0,data:await readStatus(ctx.configPath+'.monitor.json',undefined,kind)});
    }catch{res.status(503).json({code:503,message:'状态暂不可用，请检查 Bot 服务及本机状态连接配置。设置保存功能不受影响。'});}
  });
}
