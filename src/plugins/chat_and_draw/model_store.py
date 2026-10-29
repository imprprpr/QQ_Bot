"""Saved model profiles: the same JSON file the NapCat models page edits.

Location: BOT_MODEL_CONFIG_FILE when set (shared with NapCat), otherwise data/models.json.
Format: {"schema_version": 1, "revision": "...", "active": id|null,
         "profiles": [{"id", "name", "provider", "base_url", "api_key", "model"}]}
`active: null` means "use LLM_* from .env".
"""
import json
import os
from pathlib import Path
import threading
import uuid

from .config import ROOT
from .providers import ProviderError, is_loopback, normalize_base_url
from urllib.parse import urlsplit

_lock = threading.Lock()
MAX_BYTES = 256 * 1024


def config_path():
    filename = os.getenv('BOT_MODEL_CONFIG_FILE', '').strip()
    path = Path(filename) if filename else ROOT / 'data' / 'models.json'
    return path if path.is_absolute() else ROOT / path


def empty():
    return {'schema_version': 1, 'revision': '', 'active': None, 'profiles': []}


def read(path=None):
    path = path or config_path()
    if not path.exists():
        return empty()
    if path.stat().st_size > MAX_BYTES:
        raise ProviderError('模型配置文件过大')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except ValueError:
        raise ProviderError('模型配置文件损坏，请从备份恢复或删除后重新添加') from None
    if data.get('schema_version') != 1 or not isinstance(data.get('profiles'), list) or (
            data.get('active') is not None and not any(p.get('id') == data['active'] for p in data['profiles'])):
        raise ProviderError('模型配置文件格式不正确，请从备份恢复或删除后重新添加')
    return data


def public(data):
    """Profiles without secrets, for the browser."""
    return {'revision': data.get('revision', ''), 'active': data.get('active'),
            'profiles': [{k: v for k, v in p.items() if k != 'api_key'} | {'has_key': bool(p.get('api_key'))}
                         for p in data['profiles']]}


def _write(path, data):
    data['revision'] = uuid.uuid4().hex
    content = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    if len(content.encode('utf-8')) > MAX_BYTES:
        raise ProviderError('模型配置文件过大')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)  # group-readable: NapCat may share this file
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return data


def needs_key(base_url):
    return not is_loopback(urlsplit(base_url).hostname)


def mutate(operation, body, path=None):
    """save / activate / delete with optimistic revision checks (NapCat may edit the same file)."""
    path = path or config_path()
    with _lock:
        data = read(path)
        if body.get('revision', '') != data.get('revision', ''):
            raise ProviderError('CONFLICT')
        if operation == 'save':
            previous = next((p for p in data['profiles'] if p['id'] == body.get('id')), None)
            if body.get('id') and not previous:
                raise ProviderError('要修改的配置不存在，请重新读取')
            base_url = normalize_base_url(body.get('base_url'))
            name = str(body.get('name') or '').strip()
            model = str(body.get('model') or '').strip()
            key = str(body.get('api_key') or '').strip()
            if not key and previous and previous.get('base_url') == base_url:
                key = previous.get('api_key', '')  # blank keeps the saved key for the same address
            if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
                raise ProviderError('请填写配置名称（80 字以内）')
            if not model or len(model) > 200 or any(ord(c) < 32 for c in model):
                raise ProviderError('请选择或填写模型 ID')
            if needs_key(base_url) and (not key or len(key) > 4096 or any(c.isspace() for c in key)):
                raise ProviderError('请填写 API Key；更换 API 地址后需要重新填写')
            profile = {'id': previous['id'] if previous else uuid.uuid4().hex, 'name': name,
                       'provider': str(body.get('provider') or 'custom')[:40], 'base_url': base_url,
                       'api_key': key, 'model': model}
            if previous:
                data['profiles'][data['profiles'].index(previous)] = profile
            else:
                if len(data['profiles']) >= 50:
                    raise ProviderError('最多保存 50 个配置')
                data['profiles'].append(profile)
            if body.get('activate'):
                data['active'] = profile['id']
            _write(path, data)
            return data, profile['id']
        if operation == 'activate':
            identity = body.get('id')
            if identity is not None and not any(p['id'] == identity for p in data['profiles']):
                raise ProviderError('配置不存在，请重新读取')
            data['active'] = identity
            return _write(path, data), identity
        if operation == 'delete':
            identity = body.get('id')
            if data['active'] == identity:
                raise ProviderError('正在使用的配置不能删除，请先切换到其他配置')
            data['profiles'] = [p for p in data['profiles'] if p['id'] != identity]
            return _write(path, data), None
        raise ProviderError('未知操作')


def find(data, identity):
    return next((p for p in data['profiles'] if p['id'] == identity), None)
