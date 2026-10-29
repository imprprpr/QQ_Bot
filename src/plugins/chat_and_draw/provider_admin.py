"""Admin routes behind the local management page: choose, test and switch model providers."""
import asyncio
import os
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from . import model_store
from .providers import ProviderError, catalog, fill_template, list_models, normalize_base_url, provider, suggest_model, test_chat


def _fail(error):
    if str(error) == 'CONFLICT':
        raise HTTPException(409, '配置已在其他页面更新，请重新读取后再操作')
    raise HTTPException(400, str(error))


def _env_default():
    url = os.getenv('LLM_BASE_URL', '').strip()
    return {'host': urlsplit(url).hostname or '', 'model': os.getenv('LLM_MODEL', '').strip(),
            'configured': bool(os.getenv('LLM_API_KEY', '').strip() and url)}


def _target(body):
    """Resolve address and key: a blank key reuses the saved one for the same address."""
    entry = provider(str(body.get('provider') or '')) or {}
    base = body.get('base_url') or entry.get('base_url', '')
    base_url = normalize_base_url(fill_template(base, body.get('fields')) if '{' in str(base) else base)
    key = str(body.get('api_key') or '').strip()
    if not key and body.get('id'):
        saved = model_store.find(model_store.read(), body['id'])
        if saved and saved.get('base_url') == base_url:
            key = saved.get('api_key', '')
    if not key and model_store.needs_key(base_url):
        raise ProviderError('请填写 API Key')
    return entry, base_url, key


def register_provider_routes(app, guard, read_json):
    proxy = lambda: os.getenv('LLM_PROXY') or None

    @app.get('/manage/api/providers', include_in_schema=False)
    async def overview(request: Request):
        await guard(request)
        try:
            data = await asyncio.to_thread(model_store.read)
        except ProviderError as error:
            _fail(error)
        return {'catalog': catalog(), **model_store.public(data), 'env_default': _env_default(),
                'shared_with_napcat': bool(os.getenv('BOT_MODEL_CONFIG_FILE', '').strip())}

    @app.post('/manage/api/providers/models', include_in_schema=False)
    async def models(request: Request):
        await guard(request)
        body = await read_json(request, 64 * 1024)
        try:
            entry, base_url, key = _target(body)
            ids = await asyncio.to_thread(list_models, base_url, key, proxy())
        except ProviderError as error:
            _fail(error)
        return {'models': ids, 'suggested': suggest_model(entry, ids), 'base_url': base_url}

    @app.post('/manage/api/providers/test', include_in_schema=False)
    async def test(request: Request):
        await guard(request)
        body = await read_json(request, 64 * 1024)
        try:
            _, base_url, key = _target(body)
            return await asyncio.to_thread(test_chat, base_url, key, str(body.get('model') or ''), proxy())
        except ProviderError as error:
            return {'ok': False, 'error': str(error)}

    @app.post('/manage/api/providers/{operation}', include_in_schema=False)
    async def mutate(operation: str, request: Request):
        await guard(request)
        if operation not in {'save', 'activate', 'delete'}:
            raise HTTPException(404, '未知操作')
        body = await read_json(request, 64 * 1024)
        try:
            if operation == 'save':
                _, body['base_url'], _ = _target({**body, 'api_key': body.get('api_key') or 'placeholder'})
            data, identity = await asyncio.to_thread(model_store.mutate, operation, body)
        except ProviderError as error:
            _fail(error)
        return {**model_store.public(data), 'saved_id': identity}
