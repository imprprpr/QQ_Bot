"""Hot-read group/bot policy; shared with the authenticated NapCat editor."""
import json
import math
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((ROOT / 'deploy/napcat/napcat-plugin-anima-peer-manager/participation.schema.json').read_text(encoding='utf-8'))
FIELDS = {f['key']: f for f in SCHEMA}


def validate(data):
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('INVALID_PARTICIPATION_CONFIG')
    values = data.get('values')
    if not isinstance(values, dict) or set(values) != set(FIELDS):
        raise ValueError('INVALID_PARTICIPATION_FIELDS')
    for key, field in FIELDS.items():
        value = values[key]
        if field['type'] == 'boolean':
            valid = type(value) is bool
        else:
            valid = (type(value) in (int,float) and math.isfinite(value)
                     and field['min'] <= value <= field['max']
                     and (not field['integer'] or float(value).is_integer()))
        if not valid:
            raise ValueError('INVALID_PARTICIPATION_VALUE')
    return data


def managed():
    filename = os.getenv('BOT_PARTICIPATION_CONFIG_FILE','').strip()
    if not filename:
        return None
    path = Path(filename)
    if not path.is_absolute():
        path = ROOT / path
    try:
        if path.stat().st_size > 32768:
            raise ValueError()
        return validate(json.loads(path.read_text(encoding='utf-8')))['values']
    except (OSError, ValueError, TypeError):
        # Known bots remain quarantined by peer_config even while disabled.
        return {key: False if f['type']=='boolean' else f['default'] for key,f in FIELDS.items()}


def override(name):
    if name not in FIELDS:
        return None
    values = managed()
    return values[name] if values is not None else None
