"""Provider catalog, model suggestion, local fake API, profile store and admin routes."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import httpx
from fastapi import FastAPI

import test_bot  # noqa: F401  (initialises NoneBot and offline settings)
from src.plugins.chat_and_draw import admin, model_store, providers
from src.plugins.chat_and_draw.model_provider import active_profile
from src.plugins.chat_and_draw.sessions import SessionStore


class FakeOpenAI(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible server on 127.0.0.1 (no network, no key needed)."""
    key = 'sk-test'

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(raw)

    def _authorised(self):
        if self.headers.get('Authorization') != 'Bearer ' + self.key:
            self._send(401, {'error': {'message': 'Invalid API key'}}); return False
        return True

    def do_GET(self):
        if self.path == '/v1/models' and self._authorised():
            self._send(200, {'data': [{'id': 'text-embedding-3'}, {'id': 'deepseek-v4-pro'}, {'id': 'deepseek-flash'}]})

    def do_POST(self):
        if self.path == '/v1/chat/completions' and self._authorised():
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self._send(200, {'choices': [{'message': {'content': '连接成功 ' + body['model']}, 'finish_reason': 'stop'}]})

    def log_message(self, *args):
        pass


class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), FakeOpenAI)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.server.server_address[1]}/v1'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_catalog_entries_are_complete_and_remote_ones_use_https(self):
        data = providers.catalog()
        groups = {g['id'] for g in data['groups']}
        ids = [p['id'] for p in data['providers']]
        self.assertEqual(len(ids), len(set(ids)))
        for p in data['providers']:
            self.assertIn(p['group'], groups, p['id'])
            if p['id'] != 'custom':
                self.assertTrue(p['base_url'].startswith('http://127.0.0.1') if p.get('local') else p['base_url'].startswith('https://'), p['id'])
            if p.get('key_url'):
                self.assertTrue(p['key_url'].startswith('https://'), p['id'])

    def test_url_rules(self):
        self.assertEqual(providers.normalize_base_url('https://api.example.com/v1/'), 'https://api.example.com/v1')
        self.assertEqual(providers.normalize_base_url('http://localhost:11434/v1'), 'http://localhost:11434/v1')
        for bad in ['http://example.com/v1', 'ftp://x', 'https://u:p@example.com', 'https://example.com/v1?k=1',
                    'https://example.com/v1/chat/completions', 'https://{WorkspaceId}.example.com']:
            with self.assertRaises(providers.ProviderError, msg=bad):
                providers.normalize_base_url(bad)
        dashscope = providers.provider('dashscope')
        self.assertIn('ws-1', providers.fill_template(dashscope['base_url'], {'WorkspaceId': 'ws-1'}))
        with self.assertRaises(providers.ProviderError):
            providers.fill_template(dashscope['base_url'], {'WorkspaceId': 'evil.com/x'})

    def test_suggestion_prefers_hints_and_skips_non_chat_models(self):
        deepseek = providers.provider('deepseek')
        self.assertEqual(providers.suggest_model(deepseek, ['deepseek-v4-pro', 'deepseek-flash']), 'deepseek-flash')
        gemini = providers.provider('gemini')
        self.assertEqual(providers.suggest_model(gemini, ['text-embedding-004', 'gemini-3.6-flash', 'gemini-3.8-flash', 'gemini-3.9-flash-preview']), 'gemini-3.8-flash')
        self.assertIsNone(providers.suggest_model({'model_hints': []}, ['a', 'b']))

    def test_list_and_test_against_local_fake_with_readable_errors(self):
        self.assertEqual(providers.list_models(self.url, 'sk-test'), ['deepseek-flash', 'deepseek-v4-pro', 'text-embedding-3'])
        result = providers.test_chat(self.url, 'sk-test', 'deepseek-flash')
        self.assertTrue(result['ok']); self.assertIn('deepseek-flash', result['reply']); self.assertGreaterEqual(result['latency_ms'], 0)
        with self.assertRaisesRegex(providers.ProviderError, 'API Key 无效'):
            providers.list_models(self.url, 'wrong')
        with self.assertRaisesRegex(providers.ProviderError, '本机模型服务没有运行'):
            providers.list_models('http://127.0.0.1:9/v1', '')


class StoreAndRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.file = self.root / 'models.json'
        self.env = patch.dict(os.environ, BOT_MODEL_CONFIG_FILE=str(self.file), LLM_API_KEY='env-key', LLM_BASE_URL='https://api.example.com/v1', LLM_MODEL='env-model')
        self.env.start()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FakeOpenAI)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}/v1'
        self.store = SessionStore(self.root / 'state.db')
        app = FastAPI()
        admin.register_admin(app, lambda: self.store)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 5000)), base_url='http://127.0.0.1')
        self.headers = {'Authorization': 'Bearer ' + admin.admin_token(self.store)}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.server.shutdown()
        self.env.stop()
        self.tmp.cleanup()

    async def post(self, path, body):
        return await self.client.post('/manage/api/providers' + path, headers=self.headers, json=body)

    async def test_add_test_switch_and_restore_without_leaking_keys(self):
        overview = (await self.client.get('/manage/api/providers', headers=self.headers)).json()
        self.assertEqual(overview['profiles'], [])
        self.assertTrue(overview['env_default']['configured'])
        self.assertEqual((await self.client.get('/manage/api/providers')).status_code, 401)

        listed = (await self.post('/models', {'provider': 'ollama', 'base_url': self.url, 'api_key': 'sk-test'})).json()
        self.assertEqual(listed['models'][0], 'deepseek-flash')
        saved = await self.post('/save', {'revision': '', 'provider': 'custom', 'name': '本地测试', 'base_url': self.url,
                                          'api_key': 'sk-test', 'model': 'deepseek-flash', 'activate': True})
        self.assertEqual(saved.status_code, 200, saved.text)
        data = saved.json()
        self.assertNotIn('sk-test', json.dumps(data))
        self.assertEqual(active_profile()['model'], 'deepseek-flash')

        # A blank key on test/edit reuses the saved key for the same address.
        tested = (await self.post('/test', {'id': data['saved_id'], 'base_url': self.url, 'model': 'deepseek-flash'})).json()
        self.assertTrue(tested['ok'], tested)
        failed = (await self.post('/test', {'base_url': self.url, 'api_key': 'wrong', 'model': 'x'})).json()
        self.assertFalse(failed['ok']); self.assertIn('API Key', failed['error'])

        stale = await self.post('/activate', {'revision': 'old', 'id': None})
        self.assertEqual(stale.status_code, 409)
        restored = (await self.post('/activate', {'revision': data['revision'], 'id': None})).json()
        self.assertIsNone(active_profile())
        refused = await self.post('/save', {'revision': restored['revision'], 'name': 'x', 'base_url': 'http://example.com/v1', 'api_key': 'k', 'model': 'm'})
        self.assertEqual(refused.status_code, 400)
        self.assertIn('https', refused.json()['detail'])
        deleted = (await self.post('/delete', {'revision': restored['revision'], 'id': data['saved_id']})).json()
        self.assertEqual(deleted['profiles'], [])

    async def test_local_profiles_need_no_key_but_remote_ones_do(self):
        _, identity = model_store.mutate('save', {'revision': '', 'name': 'Ollama', 'provider': 'ollama',
                                                  'base_url': 'http://127.0.0.1:11434/v1', 'model': 'qwen3', 'activate': True})
        self.assertEqual(active_profile()['api_key'], '')
        revision = model_store.read()['revision']
        with self.assertRaisesRegex(providers.ProviderError, 'API Key'):
            model_store.mutate('save', {'revision': revision, 'name': 'r', 'base_url': 'https://api.example.com/v1', 'model': 'm'})
        if os.name != 'nt':  # group-readable so NapCat can share the file
            self.assertEqual(self.file.stat().st_mode & 0o777, 0o640)

    async def test_overview_reports_selected_profile(self):
        model_store.mutate('save', {'revision': '', 'name': '线路A', 'base_url': 'https://api.example.com/v1', 'api_key': 'k', 'model': 'm-a', 'activate': True})
        data = (await self.client.get('/manage/api/overview', headers=self.headers)).json()
        self.assertEqual((data['provider'], data['model']), ('线路A', 'm-a'))


class DockerAdminAccessTests(unittest.TestCase):
    def test_private_clients_only_when_explicitly_allowed(self):
        with patch.dict(os.environ, BOT_ADMIN_ALLOW_PRIVATE=''):
            self.assertTrue(admin.trusted_client('127.0.0.1'))
            self.assertFalse(admin.trusted_client('172.18.0.1'))
        with patch.dict(os.environ, BOT_ADMIN_ALLOW_PRIVATE='true'):
            self.assertTrue(admin.trusted_client('172.18.0.1'))
            for outsider in ['8.8.8.8', '169.254.1.1', 'not-an-ip']:
                self.assertFalse(admin.trusted_client(outsider), outsider)
