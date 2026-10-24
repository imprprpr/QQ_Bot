"""Hot-loaded operator profiles shared with the authenticated NapCat plugin."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from .config import ROOT, LLM_TIMEOUT
from .http_transport import request_json


def active_profile():
    filename = os.getenv('BOT_MODEL_CONFIG_FILE', '').strip()
    if not filename:
        return None
    path = Path(filename)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 256 * 1024:
            raise ValueError()
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('schema_version') != 1:
            raise ValueError()
        identity = data.get('active')
        if identity is None:
            return None
        profile = next(p for p in data['profiles'] if p['id'] == identity)
        parsed = urlsplit(profile['base_url'])
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError()
        if not profile['api_key'] or not profile['model']:
            raise ValueError()
        return dict(profile)
    except Exception:
        # Fail closed: damaged selection must not silently charge another API.
        raise ValueError('INVALID_MANAGED_MODEL_CONFIG') from None


class ManagedClient:
    def __init__(self, profile):
        self.profile = profile
        self.chat = SimpleNamespace(completions=self)

    async def create(self, *, model, messages, max_tokens=500, temperature=None, extra_body=None, **kwargs):
        profile = self.profile
        # Direct replies need room for natural text and internal media tags.
        # Preserve the separate <=180 token budget for unmentioned group chats.
        budget = max(1024, max_tokens) if max_tokens >= 500 else max_tokens
        payload = {'model': profile['model'], 'messages': messages, 'max_tokens': budget, 'stream': False}
        if temperature is not None:
            payload['temperature'] = temperature
        # Legacy provider-specific options must not leak into a newly selected API.
        async def headers(force=False):
            return {'Authorization': 'Bearer ' + profile['api_key']}
        url = profile['base_url'].rstrip('/') + '/chat/completions'
        data = await request_json(url, payload, headers, timeout=max(1, LLM_TIMEOUT-1),
            attempt_timeout=min(35, LLM_TIMEOUT), max_attempts=2,
            proxy=os.getenv('LLM_PROXY') or None, label='managed-chat',
            rate_key=url + '\0' + profile['api_key'])
        choices = data.get('choices') or []
        if not choices:
            raise ValueError('EMPTY_MANAGED_RESPONSE')
        choice = choices[0]
        usage = data.get('usage')
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason=choice.get('finish_reason'),
            message=SimpleNamespace(content=choice.get('message', {}).get('content')))],
            usage=SimpleNamespace(**usage) if isinstance(usage, dict) else None)

    async def close(self):
        pass
