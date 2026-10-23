import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_bot import plugin
from src.plugins.chat_and_draw.bot_interaction import BotInteractions, ids
from src.plugins.chat_and_draw.peer_config import managed


class PeerConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'peers.json'
        self.env = patch.dict(os.environ, BOT_INTERACTION_CONFIG_FILE=str(self.path), BOT_INTERACTION_ENABLED='true',
                              BOT_INTERACTION_TARGETS='11111', BOT_INTERACTION_GROUPS='22222')
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def save(self, targets=None, enabled=True, known=None):
        data = {'schema_version':1, 'enabled':enabled, 'targets':targets or ['33333'], 'groups':['44444']}
        if known is not None: data['known_targets']=known
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data),encoding='utf-8')
        temporary.replace(self.path)

    def test_shared_configuration_overrides_env_and_updates_without_restart(self):
        self.save()
        self.assertEqual(ids('BOT_INTERACTION_TARGETS'), {'33333'})
        self.assertTrue(BotInteractions.allowed('99999','44444','33333'))
        self.save(['55555'], known=['33333','55555'])
        self.assertEqual(ids('BOT_INTERACTION_TARGETS'), {'55555'})
        self.assertTrue(BotInteractions.is_peer('33333'))
        self.assertFalse(BotInteractions.allowed('99999','44444','33333'))

    def test_corrupt_missing_disabled_and_empty_lists_fail_closed(self):
        self.save()
        self.assertTrue(BotInteractions.is_peer('33333'))
        self.path.write_text('{invalid')
        self.assertFalse(managed()['enabled'])
        self.assertTrue(BotInteractions.is_peer('33333'))
        self.assertFalse(BotInteractions.allowed('99999','44444','33333'))
        self.path.unlink()
        self.assertFalse(managed()['enabled'])
        self.save(enabled=False)
        self.assertFalse(BotInteractions.allowed('99999','44444','33333'))

    def test_no_shared_file_keeps_legacy_config(self):
        with patch.dict(os.environ, BOT_INTERACTION_CONFIG_FILE=''):
            self.assertEqual(ids('BOT_INTERACTION_TARGETS'), {'11111'})

    def test_invalid_numbers_do_not_reach_routing(self):
        self.save(['all'])
        self.assertEqual(ids('BOT_INTERACTION_TARGETS'),set())

    def test_restart_with_broken_config_keeps_peers_quarantined(self):
        from src.plugins.chat_and_draw import peer_config
        self.path.write_text('invalid')
        Path(str(self.path)+'.known.json').write_text(json.dumps({'known_targets':['33333']}))
        peer_config._cache.clear()
        self.assertTrue(BotInteractions.is_peer('33333'))
        self.assertFalse(BotInteractions.allowed('99999','44444','33333'))
