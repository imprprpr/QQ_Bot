"""Model provider catalog, URL checks, model discovery and connection tests.

Standard library only: the first-run wizard (tools/setup.py) loads this file directly,
before the bot's own dependencies are installed. All network helpers are blocking;
the bot calls them through asyncio.to_thread.
"""
import ipaddress
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

CATALOG_FILE = Path(__file__).with_name('providers.json')
LOOPBACK_NAMES = {'localhost'}
# Model ids that are clearly not chat models; hidden from suggestions, still selectable by hand.
NOT_CHAT = re.compile(r'embed|rerank|whisper|tts|audio|speech|image|dall-e|moderation|ocr|vl-|-vl|vision-exp|transcri', re.I)


class ProviderError(Exception):
    """Operator-facing problem with a readable Chinese message."""


def catalog():
    data = json.loads(CATALOG_FILE.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1:
        raise ProviderError('提供商目录版本不兼容')
    return data


def provider(provider_id):
    return next((p for p in catalog()['providers'] if p['id'] == provider_id), None)


def is_loopback(host):
    if not host:
        return False
    if host.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.strip('[]')).is_loopback
    except ValueError:
        return False


def fill_template(base_url, fields):
    """Replace {Field} placeholders (e.g. Alibaba's workspace id) with operator input."""
    def value(match):
        text = str((fields or {}).get(match.group(1), '')).strip()
        if not re.fullmatch(r'[A-Za-z0-9-]{1,80}', text):
            raise ProviderError(f'请填写有效的 {match.group(1)}')
        return text
    return re.sub(r'\{(\w+)\}', value, base_url or '')


def normalize_base_url(value):
    """HTTPS for remote services; plain HTTP only for this computer (Ollama, LM Studio)."""
    text = str(value or '').strip()
    if '{' in text:
        raise ProviderError('地址中还有未填写的占位符，例如 {WorkspaceId}')
    try:
        url = urlsplit(text)
    except ValueError:
        raise ProviderError('请填写完整的 API 地址，例如 https://api.example.com/v1') from None
    if url.scheme not in {'https', 'http'} or not url.hostname:
        raise ProviderError('请填写完整的 API 地址，例如 https://api.example.com/v1')
    if url.username or url.password or url.query or url.fragment:
        raise ProviderError('API 地址不能包含账号密码、? 参数或 # 片段')
    if url.scheme == 'http' and not is_loopback(url.hostname):
        raise ProviderError('远程服务必须使用 https；只有本机模型（127.0.0.1 / localhost）可以用 http')
    if re.search(r'/(chat/completions|models)/?$', url.path):
        raise ProviderError('请只填写 API 基础地址，不要加 /chat/completions 或 /models')
    return text.rstrip('/')


def suggest_model(entry, model_ids):
    """Pick a sensible default from the provider's live model list using catalog hints."""
    chat = [m for m in model_ids if not NOT_CHAT.search(m)]
    hints = (entry or {}).get('model_hints') or []
    for hint in hints:
        if hint in chat:
            return hint
    for hint in hints:
        matches = [m for m in chat if hint.lower() in m.lower() and not re.search(r'preview|exp|beta', m, re.I)]
        if matches:
            # Newer versions usually sort later with natural ordering; prefer them.
            return max(matches, key=natural_key)
    return chat[0] if len(chat) == 1 else None


def natural_key(text):
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', text.lower())]


def _opener(proxy):
    handlers = [urllib.request.ProxyHandler({'http': proxy, 'https': proxy})] if proxy else []
    return urllib.request.build_opener(*handlers)


def _explain(error, base_url):
    host = urlsplit(base_url).hostname or ''
    if isinstance(error, urllib.error.HTTPError):
        body = ''
        try:
            body = error.read(2000).decode('utf-8', 'replace')
        except Exception:
            pass
        detail = ''
        try:
            parsed = json.loads(body)
            detail = str((parsed.get('error') or {}).get('message') if isinstance(parsed.get('error'), dict) else parsed.get('error') or parsed.get('message') or '')[:160]
        except Exception:
            pass
        messages = {401: 'API Key 无效或已被删除，请检查是否复制完整。',
                    403: '没有权限使用此接口或模型（可能需要实名认证或开通模型）。',
                    402: '账户余额不足，请先在提供商网站充值。',
                    404: '地址或模型不存在，请检查 API 地址和模型 ID。',
                    429: '请求太频繁或额度已用完，请稍后再试或检查套餐。'}
        text = messages.get(error.code) or f'服务返回错误 HTTP {error.code}。'
        return text + (f'（提供商说明：{detail}）' if detail else '')
    reason = getattr(error, 'reason', error)
    if isinstance(reason, TimeoutError) or 'timed out' in str(reason):
        return f'连接 {host} 超时。海外服务在国内通常需要代理（.env 中的 LLM_PROXY）。'
    if is_loopback(host):
        return f'无法连接 {host}：本机模型服务没有运行，请先启动 Ollama / LM Studio。'
    return f'无法连接 {host}：请检查网络、地址是否正确；海外服务在国内通常需要代理。'


def _request(method, url, api_key, body=None, proxy=None, timeout=20):
    headers = {'Accept': 'application/json', 'User-Agent': 'AnimaBot-setup'}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body).encode('utf-8')
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with _opener(proxy).open(request, timeout=timeout) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ProviderError(_explain(error, url)) from None
    if len(raw) > 4 * 1024 * 1024:
        raise ProviderError('服务返回的数据过大')
    try:
        return json.loads(raw)
    except ValueError:
        raise ProviderError('服务没有返回 JSON，请确认这是 OpenAI 兼容接口的基础地址。') from None


def list_models(base_url, api_key='', proxy=None, timeout=20):
    data = _request('GET', normalize_base_url(base_url) + '/models', api_key, proxy=proxy, timeout=timeout)
    items = data.get('data') if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ProviderError('此服务不支持自动列出模型，请手动填写模型 ID。')
    ids = {str(item.get('id')) for item in items if isinstance(item, dict) and item.get('id')}
    return sorted((i for i in ids if len(i) <= 200), key=natural_key)[:2000]


def test_chat(base_url, api_key, model, proxy=None, timeout=45):
    """One tiny real request. Returns latency and the reply so the operator sees it works."""
    if not str(model or '').strip():
        raise ProviderError('请先选择或填写模型 ID')
    started = time.monotonic()
    data = _request('POST', normalize_base_url(base_url) + '/chat/completions', api_key,
                    {'model': model.strip(), 'messages': [{'role': 'user', 'content': '请只回复：连接成功'}],
                     'max_tokens': 32, 'stream': False}, proxy=proxy, timeout=timeout)
    latency = round((time.monotonic() - started) * 1000)
    try:
        reply = data['choices'][0]['message'].get('content') or ''
    except (KeyError, IndexError, TypeError, AttributeError):
        raise ProviderError('服务的回复格式不符合 OpenAI 兼容接口。') from None
    return {'ok': True, 'latency_ms': latency, 'reply': str(reply).strip()[:80]}
