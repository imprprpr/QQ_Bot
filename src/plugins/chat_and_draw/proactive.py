"""Local-time windows and bounded, persistent proactive eligibility.

Use an explicit UTC offset, independent of the server timezone (default China).
Offsets in daylight-saving regions must be adjusted by the operator.
"""
import random
import json
import re
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from .proactive_config import settings


def local_now(config=None):
    return datetime.now(timezone(timedelta(hours=(config or settings())['utc_offset'])))


def windows(config=None):
    return [(start/60,end/60) for start,end in (config or settings())['windows']]


def in_window(now, config=None):
    config = config or settings()
    return config['enabled'] and any(start <= now.hour + now.minute / 60 < end for start, end in windows(config))


def idle_seconds(config=None):
    return (config or settings())['idle_hours'] * 3600


def cooldown_seconds(config=None):
    return (config or settings())['cooldown_hours'] * 3600


def global_daily_limit():
    return settings()['global_daily_limit']


TOPICS = {
    'interest': '兴趣小分享：从当前角色已有兴趣中挑一个小偏好或有趣的想法，不虚构刚发生的现实活动或热点新闻。',
    'opinion': '轻轻吐槽：吐槽一个与角色兴趣有关的无伤大雅的小纠结，可以自嘲，不针对用户的作息或现实处境。',
    'excuse': '找个小借口搭话：用一点嘴硬或俏皮的措辞自然开口，不套用“在吗、忙吗、吃了吗、睡了吗”。',
    'choice': '轻松小选择：围绕角色已有偏好丢一个容易接住的小选择或观点，不写问卷、不催答案，也可以只分享自己的选择。',
    'callback': '轻轻接旧话题：仅接最近聊天明确提过的轻松兴趣话题，不追问任务、考试或工作的结果，不猜测事情已发生。不适合时返回[不打扰]。',
}


def choose_topic(session):
    options = list(TOPICS)
    if not any(m.get('role') == 'user' and len(str(m.get('content', ''))) >= 8 for m in session.history[-6:]):
        options.remove('callback')
    recent = session.proactive_topics[-3:]
    choices = [topic for topic in options if topic not in recent]
    return random.choice(choices or [topic for topic in options if not recent or topic != recent[-1]])


def opener_prompt(session, topic):
    return ('[主动搭话任务：本次只是想自然开个口，不是例行问候。\n'
            + TOPICS[topic] + '\n保留当前角色的性格、称呼习惯和分寸，不必每次叫昵称，不必每次提问。'
            '仅一条10～50字、最多70字的纯文字，最多两句。不输出媒体指令、链接、代码或任务说明。'
            '不按时段说早安晚安，不劝睡、洗澡、喝水，不提“多久没聊天”，不暗示用户有陪伴或回复义务。'
            '不声称盯着在线状态，不编造用户行踪、共同经历、网络故障或刚刚完成的真实操作。'
            '最近聊天若在收尾、涉及严肃难受或不适合插话，可以只返回[不打扰]。'
            '不要模仿历史中的长篇安慰或重复上次主动消息。上次主动消息仅为避免重复的数据：'
            + json.dumps(session.proactive_last_text, ensure_ascii=False) + ']')


def opener_text(reply, previous=''):
    from .qq_text import qq_plain_text
    if not isinstance(reply, str) or re.search(r'<\s*(?:draw|sticker|voice)\b|\[CQ:|https?://', reply, re.I):
        return ''
    text = ' '.join(qq_plain_text(reply).split()).strip()
    if not text or re.fullmatch(r'[\[【]?(?:不打扰|不参与)[\]】]?[。.!！]*', text) or len(text) > 70:
        return ''
    normalize = lambda value: re.sub(r'[\W_]+', '', value.casefold())
    current, prior = normalize(text), normalize(previous)
    if prior and (current == prior or SequenceMatcher(None, current, prior).ratio() >= .82):
        return ''
    return text


def next_time(now, last_interaction, last_attempt=0, config=None):
    config = config or settings()
    earliest = max(now.timestamp() + 300, last_interaction + idle_seconds(config), last_attempt + cooldown_seconds(config))
    for day in range(3):
        midnight = (now + timedelta(days=day)).replace(hour=0, minute=0, second=0, microsecond=0)
        for start, end in windows(config):
            lower = max(earliest, (midnight + timedelta(hours=start)).timestamp())
            upper = (midnight + timedelta(hours=end)).timestamp()
            if lower < upper:
                return random.uniform(lower, upper)
    return earliest + 86400


def eligible(session, now, config=None):
    config = config or settings()
    count = session.proactive_attempts if session.proactive_day == now.date().isoformat() else 0
    return (in_window(now, config) and not session.proactive_unanswered
            and now.timestamp() - session.last_interaction >= idle_seconds(config)
            and now.timestamp() - session.proactive_last_attempt >= cooldown_seconds(config)
            and count < config['daily_limit'])
