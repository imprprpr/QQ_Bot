"""Saved private persona presets and per-QQ assignments, read on each request."""
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
TEXT_FIELDS={'name':80,'description':6000,'personality':6000,'scenario':3000,
             'first_mes':1000,'mes_example':8000,'system_prompt':6000}


def read():
    filename=os.getenv('BOT_PERSONA_CONFIG_FILE','').strip()
    if not filename:return {'schema_version':1,'revision':'','profiles':[],'bindings':[]}
    path=Path(filename)
    if not path.is_absolute():path=ROOT/path
    try:
        if path.stat().st_size>1024*1024:raise ValueError()
        data=json.loads(path.read_text(encoding='utf-8'))
        if data.get('schema_version')!=1 or not isinstance(data.get('profiles'),list) or not isinstance(data.get('bindings'),list):raise ValueError()
        if len(data['profiles'])>50 or len(data['bindings'])>1000:raise ValueError()
        import re
        seen=set()
        for p in data['profiles']:
            if not re.fullmatch(r'persona-[a-f0-9-]{36}',p['id']) or p['id'] in seen:raise ValueError()
            seen.add(p['id'])
            if not isinstance(p.get('base_card',''),str) or len(p.get('base_card',''))>80 or p.get('base_card','').startswith('persona-'):raise ValueError()
            for field,limit in TEXT_FIELDS.items():
                if not isinstance(p.get(field),str) or len(p[field])>limit:raise ValueError()
            if not p['name'].strip() or not (p['description'].strip() or p['personality'].strip()):raise ValueError()
        keys=set()
        for b in data['bindings']:
            if not re.fullmatch(r'[1-9][0-9]{0,19}',b['user_id']) or not (b['bot_id']=='*' or re.fullmatch(r'[1-9][0-9]{0,19}',b['bot_id'])):raise ValueError()
            if not isinstance(b['persona_id'],str) or not b['persona_id'] or len(b['persona_id'])>100:raise ValueError()
            key=(b['bot_id'],b['user_id'])
            if key in keys:raise ValueError()
            keys.add(key)
        return data
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        raise ValueError('PERSONA_CONFIG_UNAVAILABLE') from None


def binding(bot_id,user_id):
    entries=read()['bindings']
    for scope in (str(bot_id),'*'):
        selected=next((b['persona_id'] for b in entries if b['bot_id']==scope and b['user_id']==str(user_id)),None)
        if selected:return selected
    return None


def cards(builtins=None):
    from .tavern import TavernCard
    if builtins is None:
        from .tavern import tavern_manager
        builtins=tavern_manager.cards
    result={}
    for p in read()['profiles']:
        base=builtins.get(p.get('base_card',''))
        raw=dict(base.raw_data.get('data',base.raw_data)) if base else {}
        raw.update({k:p[k] for k in TEXT_FIELDS})
        result[p['id']]=TavernCard(raw,p['id']+'.json')
    return result


def card(persona_id):
    value=cards().get(persona_id)
    if not value:raise ValueError('ASSIGNED_PERSONA_NOT_FOUND')
    return value


def catalog(path):
    from .tavern import tavern_manager
    from .sessions import SessionStore
    tavern_manager.reload_cards()
    unique={Path(c.filepath).stem:c for c in tavern_manager.cards.values() if c.filepath and not Path(c.filepath).stem.startswith('persona-')}
    store=SessionStore(path);store.initialize()
    with store.database.transaction() as conn:
        data=[json.loads(r[0]) for r in conn.execute('SELECT data FROM sessions')]
    bots={str(s['bot_id']) for s in data if s.get('bot_id')}
    from nonebot import get_bots
    bots.update(get_bots())
    return {'schema_version':1,'cards':[{'id':key,**{f:getattr(c,f,'') for f in TEXT_FIELDS}} for key,c in unique.items()],
            'bot_ids':sorted(bots)}
