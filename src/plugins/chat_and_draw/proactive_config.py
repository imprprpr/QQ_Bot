"""Hot-read proactive policy. A configured but damaged file fails closed."""
import hashlib
import json
import math
import os
from pathlib import Path

from .config import ROOT


def validate(data):
    if not isinstance(data, dict) or data.get('schema_version') != 1 or type(data.get('enabled')) is not bool:
        raise ValueError('INVALID_PROACTIVE_CONFIG')
    bounds = {'utc_offset':(-12,14), 'idle_hours':(1,24), 'cooldown_hours':(3,48), 'daily_limit':(0,3), 'global_daily_limit':(0,20)}
    for key, (lower, upper) in bounds.items():
        value = data.get(key)
        if type(value) not in (int,float) or not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError('INVALID_PROACTIVE_CONFIG')
        if key.endswith('limit') and type(value) is not int:
            raise ValueError('INVALID_PROACTIVE_CONFIG')
    windows = data.get('windows')
    if not isinstance(windows,list) or not 1 <= len(windows) <= 8:
        raise ValueError('INVALID_PROACTIVE_CONFIG')
    for item in windows:
        if not isinstance(item,list) or len(item) != 2 or any(type(v) is not int for v in item):
            raise ValueError('INVALID_PROACTIVE_CONFIG')
    end = -1
    for item in sorted(windows):
        start, stop = item
        if not 0 <= start < stop <= 1440 or start < end:
            raise ValueError('INVALID_PROACTIVE_CONFIG')
        end = stop
    return {**data, 'windows':sorted(windows)}


def settings():
    legacy_windows = [[480,1320]] if os.getenv('PROACTIVE_MODE','windows') == 'random' else [[480,600],[720,840],[1140,1320]]
    default = {'schema_version':1,'enabled':True,'utc_offset':float(os.getenv('PROACTIVE_UTC_OFFSET','8')),
        'windows':legacy_windows,'idle_hours':max(1,float(os.getenv('PROACTIVE_IDLE_HOURS','3'))),
        'cooldown_hours':max(3,float(os.getenv('PROACTIVE_COOLDOWN_HOURS','6'))),
        'daily_limit':max(0,min(3,int(os.getenv('PROACTIVE_DAILY_LIMIT','2')))),
        'global_daily_limit':max(0,min(20,int(os.getenv('PROACTIVE_GLOBAL_DAILY_LIMIT','6')))), '_revision':''}
    filename = os.getenv('BOT_PROACTIVE_CONFIG_FILE','').strip()
    if not filename:
        return default
    path = Path(filename)
    if not path.is_absolute():
        path = ROOT / path
    try:
        if path.stat().st_size > 32768:
            raise ValueError()
        raw = path.read_bytes()
        return {**validate(json.loads(raw)), '_revision':hashlib.sha256(raw).hexdigest()}
    except (OSError,ValueError,TypeError,KeyError):
        return {**default,'enabled':False,'windows':[], '_revision':'unavailable'}
