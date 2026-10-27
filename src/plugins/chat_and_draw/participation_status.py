"""Bounded, content-free decisions and live counters for the authenticated UI."""
import json
import time
from .database import database

REASONS = {
    'generating':'已通过筛选，生成中', 'sent':'QQ发送接口返回成功', 'queued':'图片任务已排队，尚未确认送达', 'model_declined':'模型选择不参与',
    'empty_result':'模型返回空结果', 'model_timeout':'模型请求超时', 'provider_rate_limited':'模型服务限流',
    'service_error':'模型请求失败', 'output_limit':'模型输出达到上限', 'send_failed':'QQ发送失败或未确认',
    'cancelled':'配置改变或任务已失效', 'disabled':'参与已关闭或不在允许名单',
    'not_addressed':'消息指向别人或不属于普通聊天', 'not_sampled':'未抽中随机参与',
    'inactive':'群聊活跃度不足', 'cooldown':'冷却中', 'rounds':'接话/互聊轮次已满',
    'room_hourly':'本群小时额度已满', 'room_daily':'本群24小时额度已满',
    'global_hourly':'全局小时额度已满', 'global_daily':'全局24小时额度已满',
    'proactive_daily':'主动开场24小时额度已满', 'waiting':'本轮正在处理或已结束',
    'duplicate':'重复事件已忽略',
}
MODES = {'mention','nickname','followup','ambient','bot-interaction','bot-proactive'}
SCHEMA = '''CREATE TABLE IF NOT EXISTS participation_decisions (
 id INTEGER PRIMARY KEY, room TEXT, participant TEXT, mode TEXT, reason TEXT,
 created REAL, updated REAL, retry_at REAL, external_id TEXT);
 CREATE INDEX IF NOT EXISTS participation_decision_room ON participation_decisions(room,created);'''


class DecisionLog:
    def __init__(self,path):
        self.database=database(path)

    def initialize(self):
        self.database.initialize('participation-decisions-v1',SCHEMA)

    @staticmethod
    def write(conn,room,participant,mode,reason,now,retry_at=0,external_id=None):
        if mode not in MODES or reason not in REASONS:
            raise ValueError('INVALID_DECISION')
        conn.execute('DELETE FROM participation_decisions WHERE created<?',(now-86400,))
        cursor=conn.execute('INSERT INTO participation_decisions(room,participant,mode,reason,created,updated,retry_at,external_id) VALUES(?,?,?,?,?,?,?,?)',
            (room,str(participant)[:30],mode,reason,now,now,max(0,retry_at),str(external_id)[:80] if external_id is not None else ''))
        conn.execute('DELETE FROM participation_decisions WHERE id NOT IN (SELECT id FROM participation_decisions ORDER BY id DESC LIMIT 3000)')
        return cursor.lastrowid

    def add(self,room,participant,mode,reason,now=None,retry_at=0,external_id=None):
        self.initialize()
        with self.database.transaction() as conn:
            return self.write(conn,room,participant,mode,reason,time.time() if now is None else now,retry_at,external_id)

    def finish(self,context,reason):
        if not context or not context.get('decision_id'):
            return
        if reason not in REASONS:
            raise ValueError('INVALID_DECISION')
        self.initialize()
        with self.database.transaction() as conn:
            conn.execute('UPDATE participation_decisions SET reason=?,updated=? WHERE id=?',
                (reason,time.time(),context['decision_id']))


def snapshot(path,now=None):
    """Read all counters in one short transaction; no model calls or reservations."""
    from .group_chat import GroupChat, number
    from .bot_interaction import BotInteractions
    from .config import feature_enabled
    groups=GroupChat(path)
    bots=BotInteractions(path)
    # Initialize outside the snapshot transaction; never nest SQLite writers.
    with groups.connect():pass
    bots.initialize()
    log=DecisionLog(path);log.initialize()
    now=time.time() if now is None else now
    with log.database.transaction() as conn:
        group_calls=[dict(r) for r in conn.execute('SELECT room,mode,created FROM group_chat_calls WHERE created>?',(now-86400,))]
        bot_calls=[dict(r) for r in conn.execute('SELECT room,kind,created FROM bot_interaction_calls WHERE created>?',(now-86400,))]
        limits=[dict(r) for r in conn.execute('SELECT * FROM group_chat_mode_limits WHERE last_attempt>?',(now-86400,))]
        ambient_limits={r['room']:r['last_attempt'] for r in conn.execute('SELECT * FROM group_chat_limits WHERE last_attempt>?',(now-86400,))}
        turns=[dict(r) for r in conn.execute('SELECT * FROM group_chat_turns WHERE created>?',(now-600,))]
        states=[dict(r) for r in conn.execute('SELECT room,peer,rounds,active_until,last_attempt,waiting FROM bot_interaction_rooms')]
        events=[dict(r) for r in conn.execute('SELECT * FROM participation_decisions WHERE created>? ORDER BY id DESC LIMIT 200',(now-86400,))]

    def quota(rows,limit,seconds):
        used=sum(row['created']>now-seconds for row in rows)
        times=sorted(row['created']+seconds for row in rows if row['created']>now-seconds)
        # When a limit is reduced below usage, wait for enough attempts to expire.
        reset=times[used-int(limit)] if limit>0 and used>=limit else 0
        return {'used':used,'limit':int(limit),'remaining':max(0,int(limit)-used),
                'retry_at':reset,'paused':limit==0}

    def metrics(room=None):
        result={}
        for mode,defaults in {'nickname':(20,40,60,120),'followup':(30,60,120,240),'ambient':(4,10,12,24)}.items():
            prefix='GROUP_CHAT_'+mode.upper()+'_'
            rows=[r for r in group_calls if r['mode']==mode and (room is None or r['room']==room)]
            global_key='GLOBAL_' if room is None else ''
            enabled=feature_enabled('GROUP_CHAT_ENABLED') and (mode=='nickname' or feature_enabled('GROUP_CHAT_'+mode.upper()+'_ENABLED'))
            result[mode]={'enabled':enabled,'hourly':quota(rows,number(prefix+global_key+'HOURLY_LIMIT',defaults[1 if room is None else 0]),3600),
                          'daily':quota(rows,number(prefix+global_key+'DAILY_LIMIT',defaults[3 if room is None else 2]),86400)}
        rows=[r for r in bot_calls if room is None or r['room']==room]
        from .peer_config import managed as peer_policy
        peer_config=peer_policy()
        result['bots']={'enabled':feature_enabled('BOT_INTERACTION_ENABLED') and (peer_config is None or peer_config['enabled']),
            'daily':quota(bot_calls,number('BOT_INTERACTION_DAILY_LIMIT',12),86400),
            'proactive_enabled':feature_enabled('BOT_INTERACTION_PROACTIVE_ENABLED'),
            'proactive_daily':quota([r for r in bot_calls if r['kind']=='proactive'],number('BOT_INTERACTION_PROACTIVE_DAILY_LIMIT',3),86400)}
        if room is not None:result['bots']['hourly']=quota(rows,number('BOT_INTERACTION_HOURLY_LIMIT',6),3600)
        return result

    rooms=set(r['room'] for r in group_calls+bot_calls+limits+states+events)|set(ambient_limits)
    # Include configured bot rooms before their first interaction.
    from .bot_interaction import ids
    try:
        from nonebot import get_bots
        for bot in get_bots().values():
            for group in sorted(ids('BOT_INTERACTION_GROUPS'))[:100]:rooms.add(bots.room(bot.self_id,group))
    except ValueError:pass
    room_data=[]
    for room in sorted(rooms)[:200]:
        try:bot_id,group_id=json.loads(room)
        except (ValueError,TypeError):continue
        members={r['participant'] for r in limits+events if r['room']==room and r['participant']}
        member_data=[]
        for member in sorted(members)[:200]:
            last=max((r['last_attempt'] for r in limits if r['room']==room and r['participant']==member),default=0)
            recent=[r for r in turns if r['room']==room and r['participant']==member and r['created']>now-number('GROUP_CHAT_FOLLOWUP_WINDOW',300)]
            round_limit=number('GROUP_CHAT_FOLLOWUP_MAX_ROUNDS',6)
            member_data.append({'id':member,'nickname_until':last+number('GROUP_CHAT_NICKNAME_COOLDOWN',45) if last else 0,
                'followup_until':last+number('GROUP_CHAT_FOLLOWUP_COOLDOWN',5) if last else 0,
                'rounds_used':len(recent),'rounds_limit':int(round_limit),
                'rounds_retry_at':sorted(r['created']+number('GROUP_CHAT_FOLLOWUP_WINDOW',300) for r in recent)[len(recent)-int(round_limit)] if recent and len(recent)>=round_limit else 0})
        state=next((s for s in states if s['room']==room),None)
        if state:
            active=now<state['active_until']
            state={**state,'active':active,'rounds_limit':bots.max_rounds(),
                'cooldown_until':state['last_attempt']+number('BOT_INTERACTION_REPLY_COOLDOWN',5,1) if active and state['rounds']<bots.max_rounds() else state['active_until']+number('BOT_INTERACTION_COOLDOWN',600,60),
                'next_session_at':state['active_until']+number('BOT_INTERACTION_COOLDOWN',600,60)}
        room_data.append({'id':room,'bot_id':str(bot_id),'group_id':str(group_id),'quotas':metrics(room),
            'ambient_until':ambient_limits.get(room,0)+number('GROUP_CHAT_COOLDOWN',180) if room in ambient_limits else 0,
            'members':member_data,'bot_state':state})
    for event in events:
        event['label']=REASONS[event['reason']]
        if event['reason']=='generating' and now-event['updated']>300:
            event['label']='生成状态未结束（可能已中断），请查看日志'
    return {'schema_version':1,'server_time':now,'quotas':metrics(),'rooms':room_data,'events':events,
            'history_started':'本功能上线后记录；无历史记录不能推断未回复原因',
            'limits_note':'统计生成尝试而非实际消息/HTTP重试次数；小时和24小时均为滚动窗口。'}
