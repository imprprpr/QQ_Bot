import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_bot
from src.plugins.chat_and_draw.drawing import api_image_prompt, drawing_context
from src.plugins.chat_and_draw.runtime import current_scope
from src.plugins.chat_and_draw.tavern import tavern_manager


class PhotoStyleTests(unittest.TestCase):
    def test_ling_appearance_and_photo_style_apply_to_direct_requests(self):
        token=current_scope.set(SimpleNamespace(character='ling'))
        try:
            with patch.dict(os.environ,{'DRAW_API_STYLE':'photography'}):
                prompt=api_image_prompt('The current companion reading by a window.')
        finally:current_scope.reset(token)
        self.assertIn('long smooth black hair',prompt)
        self.assertIn('natural dark brown eyes',prompt)
        self.assertIn('realistic skin texture',prompt)
        self.assertIn('adult woman in her twenties',prompt)

    def test_queued_role_scope_does_not_use_other_active_character(self):
        token=current_scope.set(SimpleNamespace(character='amelia'))
        try:
            with patch.dict(os.environ,{'DRAW_API_STYLE':'photography'}):prompt=api_image_prompt('A portrait of the companion.')
        finally:current_scope.reset(token)
        self.assertIn('long blonde hair',prompt)
        self.assertIn('natural blue eyes',prompt)
        self.assertNotIn('long smooth black hair',prompt)

    def test_other_subjects_not_forced_to_be_character(self):
        with patch.dict(os.environ,{'DRAW_API_STYLE':'photography'}):prompt=api_image_prompt('A mountain lake at dawn, no people.')
        self.assertIn('A mountain lake at dawn, no people.',prompt)
        self.assertIn('Do not insert this person into landscapes',prompt)
        self.assertIn("Keep the requested scene's time",prompt)

    def test_opt_out_preserves_original_prompt(self):
        with patch.dict(os.environ,{'DRAW_API_STYLE':'custom'}):self.assertEqual(api_image_prompt('scene'),'scene')

    def test_chat_prompt_receives_role_profile_and_photographic_instructions(self):
        with patch.dict(os.environ,{'DRAW_ENABLED':'true','DRAW_BACKEND':'api','DRAW_API_STYLE':'photography'}):
            prompt=drawing_context('red dress at sunset',tavern_manager.cards['ling'])
        self.assertIn('long smooth black hair',prompt)
        self.assertIn('日系生活人像摄影',prompt)
        self.assertIn('red dress at sunset',prompt)
        self.assertNotIn('完整英文 Danbooru 风格标签',prompt)

    def test_comfy_workflow_prompt_is_not_changed_to_photo(self):
        with patch.dict(os.environ,{'DRAW_ENABLED':'true','DRAW_BACKEND':'comfyui','DRAW_API_STYLE':'photography'}):
            prompt=drawing_context()
        self.assertIn('Danbooru',prompt)
        self.assertNotIn('固定摄影风格',prompt)
