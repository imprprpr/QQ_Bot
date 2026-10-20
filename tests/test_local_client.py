"""Private-adapter extension tests use temporary files and fake clients only."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_bot import llm
from src.plugins.chat_and_draw import local_client as adapter


class LocalClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await adapter.close_local_clients()
        adapter._factories.clear()

    async def test_default_api_backend_has_no_custom_client(self):
        with patch.dict(os.environ, {"LLM_CLIENT_FACTORY": ""}):
            self.assertIsNone(adapter.local_client())
            self.assertEqual(llm.provider_name(), "openai-compatible")

    async def test_private_factory_handles_chat_and_vision_and_close(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "client.py"
            file.write_text("class Client:\n    closed = False\n    async def close(self):\n        self.closed = True\n"
                            "def create_client(vision=False):\n    c = Client()\n    c.vision = vision\n    return c\n")
            with patch.dict(os.environ, {"LLM_CLIENT_FACTORY": str(file), "LLM_BACKEND_LABEL": "private-test"}):
                chat = llm.get_client()
                vision = llm.get_client(vision=True)
                self.assertFalse(chat.vision)
                self.assertTrue(vision.vision)
                self.assertIs(llm.get_client(), chat)
                self.assertEqual(llm.provider_name(), "private-test")
                await llm.close_clients()
                self.assertTrue(chat.closed)
                self.assertTrue(vision.closed)

    async def test_bad_private_path_never_falls_back_to_public_api(self):
        with patch.dict(os.environ, {"LLM_CLIENT_FACTORY": "missing-private-adapter.py"}), \
             patch.object(llm, "AsyncOpenAI") as sdk:
            with self.assertRaises(FileNotFoundError):
                llm.get_client()
            sdk.assert_not_called()
