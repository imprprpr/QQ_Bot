"""Read-only, hot-reloaded NapCat peer lists. Invalid files disable interactions."""
import json
import os
from pathlib import Path
import re
from threading import RLock

_lock = RLock()
_cache = {}


def _ids(value):
    if not isinstance(value, list) or len(value) > 1000:
        raise ValueError('INVALID_PEER_LIST')
    if any(not isinstance(item, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', item) for item in value):
        raise ValueError('INVALID_PEER_ID')
    return set(value)


def managed():
    filename = os.getenv('BOT_INTERACTION_CONFIG_FILE', '').strip()
    if not filename:
        return None
    path = Path(filename).resolve()
    with _lock:
        old = _cache.get(str(path), {'known': set()})
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            if old.get('signature') == signature:
                return old
            if stat.st_size > 256 * 1024:
                raise ValueError('PEER_CONFIG_TOO_LARGE')
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('schema_version') != 1 or not isinstance(data.get('enabled'), bool):
                raise ValueError('INVALID_PEER_CONFIG')
            targets, groups = _ids(data.get('targets')), _ids(data.get('groups'))
            known = _ids(data.get('known_targets', data.get('targets')))
            result = {'enabled': data['enabled'], 'targets': targets, 'groups': groups,
                      'known': old['known'] | known | targets, 'signature': signature}
        except (OSError, ValueError, TypeError, AttributeError):
            # Keep known bots quarantined from the unlimited human @ path.
            known = set(old['known'])
            try:
                quarantine = Path(str(path) + '.known.json')
                if quarantine.stat().st_size <= 256 * 1024:
                    known |= _ids(json.loads(quarantine.read_text(encoding='utf-8')).get('known_targets'))
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            result = {'enabled': False, 'targets': set(), 'groups': set(), 'known': known}
        _cache[str(path)] = result
        return result
