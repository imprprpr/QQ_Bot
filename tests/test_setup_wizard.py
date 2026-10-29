"""First-run wizard: .env writing, --check and a non-interactive run against a local fake API.

Standard library only (the wizard must work before dependencies are installed)."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('setup_wizard', ROOT / 'tools' / 'setup_wizard.py')
wizard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wizard)


class Fake(BaseHTTPRequestHandler):
    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        self._send(200, {'data': [{'id': 'deepseek-v4-pro'}, {'id': 'deepseek-flash'}]})

    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self._send(200, {'choices': [{'message': {'content': '连接成功'}}]})

    def log_message(self, *args):
        pass


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / '.env'

    def tearDown(self):
        self.tmp.cleanup()

    def run_wizard(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = wizard.main([*args, '--env-file', str(self.env)])
        return code, out.getvalue()

    def test_write_env_updates_in_place_keeps_comments_and_backs_up(self):
        self.env.write_text('# keep me\nLLM_MODEL=old\nOTHER=1\n', encoding='utf-8')
        wizard.write_env(self.env, {'LLM_MODEL': 'new', 'PORT': '8081'})
        text = self.env.read_text(encoding='utf-8')
        self.assertIn('# keep me\nLLM_MODEL=new\nOTHER=1\n', text)
        self.assertIn('PORT=8081', text)
        self.assertIn('LLM_MODEL=old', self.env.with_name('.env.bak').read_text(encoding='utf-8'))

    def test_check_reports_missing_and_local_models_need_no_key(self):
        empty = Path(self.tmp.name)
        self.assertFalse(wizard.configured({}, empty))
        self.assertFalse(wizard.configured({'LLM_BASE_URL': 'https://api.deepseek.com', 'LLM_MODEL': 'm'}, empty))
        self.assertTrue(wizard.configured({'LLM_BASE_URL': 'https://api.deepseek.com', 'LLM_MODEL': 'm', 'LLM_API_KEY': 'k'}))
        self.assertTrue(wizard.configured({'LLM_BASE_URL': 'http://127.0.0.1:11434/v1', 'LLM_MODEL': 'qwen3'}, empty))
        (empty / 'data').mkdir()
        (empty / 'data' / 'models.json').write_text('{"active": "x"}', encoding='utf-8')
        self.assertTrue(wizard.configured({}, empty))  # a profile chosen in the admin page counts

    def test_non_interactive_run_picks_recommended_model_and_writes_qq_settings(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Fake)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f'http://127.0.0.1:{server.server_address[1]}/v1'
            self.assertEqual(self.run_wizard('--check')[0], 2)
            # The deepseek preset's hints pick deepseek-flash from the live list.
            code, out = self.run_wizard('--provider', 'deepseek', '--base-url', url, '--api-key', 'sk-x', '--yes', '--port', '8099')
            self.assertEqual(code, 0, out)
            values = wizard.read_env(self.env)
            self.assertEqual(values['LLM_MODEL'], 'deepseek-flash')
            self.assertEqual(values['PORT'], '8099')
            self.assertGreaterEqual(len(values['ONEBOT_ACCESS_TOKEN']), 20)
            self.assertEqual(values['DRAW_ENABLED'], 'false')
            self.assertIn('ws://127.0.0.1:8099/onebot/v11/ws', out)
            self.assertNotIn('sk-x', out)
            self.assertEqual(self.run_wizard('--check')[0], 0)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
