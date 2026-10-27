"""Read-only proactive eligibility; never randomizes or advances a schedule."""
import json
import time
from datetime import datetime,timedelta,timezone
from .proactive_config import settings
from .sessions import SessionStore

RESULTS={'generating':'正在生成','sending':'正在发送','sent':'QQ发送接口返回成功',
         'model_declined':'模型选择不打扰','filtered':'结果重复、过长或不适合发送','empty_result':'空结果',
         'model_timeout':'模型超时','provider_rate_limited':'模型服务限流','service_error':'生成/处理失败',
         'output_limit':'模型输出达到上限','send_failed':'QQ发送失败或未确认','cancelled':'用户已聊天或设置/角色发生变化'}


def next_window(epoch,config):
    zone=timezone(timedelta(hours=config['utc_offset']))
    at=datetime.fromtimestamp(epoch,zone)
    for day in range(8):
        midnight=(at+timedelta(days=day)).replace(hour=0,minute=0,second=0,microsecond=0)
        for start,end in config['windows']:
            lower=max(epoch,(midnight+timedelta(minutes=start)).timestamp())
            if lower<(midnight+timedelta(minutes=end)).timestamp():return lower
    return None


def snapshot(path,epoch=None):
    epoch=time.time() if epoch is None else epoch
    config=settings();zone=timezone(timedelta(hours=config['utc_offset']))
    now=datetime.fromtimestamp(epoch,zone);day=now.date().isoformat()
    midnight=(now+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    store=SessionStore(path);store.initialize()
    with store.database.transaction() as conn:
        data=[json.loads(r[0]) for r in conn.execute('SELECT data FROM sessions')]
    private=[s for s in data if s.get('private',True)]
    used=sum(s.get('proactive_attempts',0) for s in private if s.get('proactive_day')==day)
    rows=[]
    for s in private:
        count=s.get('proactive_attempts',0) if s.get('proactive_day')==day else 0
        cooldown=s.get('proactive_last_attempt',0)+config['cooldown_hours']*3600 if s.get('proactive_last_attempt') else 0
        idle=s.get('last_interaction',0)+config['idle_hours']*3600
        scheduled=s.get('next_proactive',0)
        reasons=[];candidate=max(epoch,cooldown,idle,scheduled)
        if not config['enabled']:reasons.append('全局主动聊天已关闭或配置不可用')
        if not s.get('proactive',True):reasons.append('此会话已单独关闭')
        if s.get('proactive_unanswered'):reasons.append('上次主动消息尚未回复，暂停继续打扰')
        if count>=config['daily_limit']:
            reasons.append('此会话今日额度已用完' if config['daily_limit'] else '此会话每日额度为0，已暂停')
            candidate=max(candidate,midnight)
        if used>=config['global_daily_limit']:
            reasons.append('全局今日额度已用完' if config['global_daily_limit'] else '全局每日额度为0，已暂停')
            candidate=max(candidate,midnight)
        if cooldown>epoch:reasons.append('主动尝试冷却未结束')
        if idle>epoch:reasons.append('距离上次聊天尚未达到闲置时间')
        if not any(start<=now.hour*60+now.minute<end for start,end in config['windows']):
            reasons.append('当前不在允许搭话的时间段')
        pending=bool(config['_revision'] and s.get('proactive_schedule_revision')!=config['_revision'])
        if pending:reasons.append('设置已更新，等待后台重新排程')
        elif scheduled>epoch:reasons.append('尚未到随机安排的搭话时间')
        elif not scheduled:reasons.append('尚未建立搭话计划，需先有一次正常私聊')
        if not reasons:reasons.append('已满足条件，等待下一次后台检查（每5分钟一次）')
        paused=(not config['enabled'] or not s.get('proactive',True) or s.get('proactive_unanswered')
                or not config['daily_limit'] or not config['global_daily_limit'] or not scheduled)
        expected=None if paused or pending else next_window(candidate,config)
        result=s.get('proactive_last_result','')
        label=RESULTS.get(result,'暂无记录（上线前原因不推测）')
        if result in {'generating','sending'} and epoch-s.get('proactive_last_result_at',0)>300:
            label='上次处理未结束，可能已中断，请查看日志'
        try:
            key=json.loads(s['key']);bot_id=s.get('bot_id') or key[0];user_id=s.get('user_id') or key[-1]
        except (ValueError,TypeError,KeyError,IndexError):bot_id=s.get('bot_id','');user_id=s.get('user_id','')
        rows.append({'bot_id':str(bot_id),'user_id':str(user_id),'character':s.get('character'),
            'used':count,'remaining':max(0,config['daily_limit']-count),'limit':config['daily_limit'],
            'enabled':bool(s.get('proactive',True)),'unanswered':bool(s.get('proactive_unanswered')),
            'cooldown_until':cooldown,'idle_until':idle,'scheduled_at':scheduled,'expected_at':expected,
            'reasons':reasons,'last_result':label,'last_result_at':s.get('proactive_last_result_at',0)})
    return {'schema_version':1,'server_time':epoch,'quota_day':day,'utc_offset':config['utc_offset'],
        'reset_at':midnight,'enabled':config['enabled'],'used':used,'limit':config['global_daily_limit'],
        'remaining':max(0,config['global_daily_limit']-used),'sessions':rows,
        'note':'次数按所配置时区的自然日统计，包含失败/跳过尝试；预计时间为当前条件下最早可尝试时间，后台每5分钟检查，模型仍可选择不打扰。'}
