"""Run in the separate Genie environment with GENIE_DATA_DIR configured."""
import unittest
from unittest.mock import patch

from genie_tts.G2P.Chinese.ChineseG2P import chinese_to_phones
from genie_tts.G2P.Chinese.ToneSandhi import ToneSandhi


class ChineseFrontendTests(unittest.TestCase):
    def test_common_conversation_with_syllabic_interjection(self):
        for text in ["你好。", "嗯，很好。", "嗯，你想买点什么？", "嗯，想你了。", "你今天吃饭了吗？"]:
            with self.subTest(text=text):
                clean, symbols, phones, counts = chinese_to_phones(text)
                self.assertTrue(phones)
                self.assertEqual(sum(counts), len(phones))

    def test_empty_final_does_not_merge_or_crash(self):
        with patch("genie_tts.G2P.Chinese.ToneSandhi.lazy_pinyin", return_value=[""]):
            result = ToneSandhi()._merge_continuous_three_tones_2([("嗯", "e"), ("你", "r")])
        self.assertEqual(len(result), 2)

    def test_valid_third_tones_still_merge(self):
        result = ToneSandhi()._merge_continuous_three_tones_2([("你", "r"), ("好", "a")])
        self.assertEqual(result[0][0], "你好")
