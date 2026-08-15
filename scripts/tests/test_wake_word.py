"""
tests/test_wake_word.py
------------------------
13日目②:`wake_word.detect_wake_word` のユニットテスト。

「クレア/ねえクレア」の表記ゆれ・Voskの誤認識パターンを検出できるか、
ウェイクワードが無い場合にNoneを返すか、マッチ後の本文を正しく切り出せるかを検証する。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
for p in (SCRIPTS_DIR, TESTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from wake_word import detect_wake_word  # noqa: E402


class TestDetectWakeWord(unittest.TestCase):
    def test_detects_plain_name(self):
        d = detect_wake_word("クレア 明日の天気を教えて")
        self.assertIsNotNone(d)
        self.assertEqual(d.text_after, "明日の天気を教えて")

    def test_detects_nee_claire_and_hiragana_and_ascii(self):
        for text in ["ねえクレア、今何時?", "ねぇくれあ 今何時?", "hey claire what time is it"]:
            self.assertIsNotNone(detect_wake_word(text), text)

    def test_detects_common_vosk_misrecognitions(self):
        # Voskの暫定結果で実際に出うる誤変換パターン(実機で出たものを随時追加する)
        for text in ["暮れ亜、こんにちは", "クレヤ こんにちは", "クレアさん こんにちは"]:
            self.assertIsNotNone(detect_wake_word(text), text)

    def test_returns_none_without_wake_word(self):
        self.assertIsNone(detect_wake_word("明日の天気を教えて"))

    def test_text_after_is_empty_when_only_called(self):
        d = detect_wake_word("クレア")
        self.assertIsNotNone(d)
        self.assertEqual(d.text_after, "")

    def test_detects_hey_and_iyaa_and_yaa_prefixes_with_separator(self):
        # 16日目 修正1: 「へい、クレア」「いやー、クレア」「やー、クレア」に対応する。
        # 前置き語とクレア本体の間に読点/空白が挟まっても検出できることを確認する。
        for text in ["へい、クレア 今何時?", "いやー、クレア 今何時?", "やー、クレア 今何時?"]:
            self.assertIsNotNone(detect_wake_word(text), text)

    def test_detects_kidou_suffix_with_separator(self):
        # 16日目 修正1: 「クレア、きどう」(起動コマンド)に対応する。
        # 語尾語もクレア本体との間に読点が挟まっても検出できることを確認する。
        d = detect_wake_word("クレア、きどう")
        self.assertIsNotNone(d)
        self.assertEqual(d.text_after, "")

    def test_detects_nee_claire_with_comma_separator(self):
        # 既存の「ねえクレア」(区切りなし)に加え、読点区切りでも検出できることを確認する。
        d = detect_wake_word("ねえ、クレア 明日の天気は?")
        self.assertIsNotNone(d)

    def test_detects_dotted_spelled_out_ascii_name(self):
        # 18日目 修正: UIに表示/入力される「C.L.A.I.R.E.」のように1文字ずつピリオドで
        # 区切ったスペルアウト表記(旧実装では`claire`と連続一致せず未検出だった)を
        # 検出できることを確認する。検出できないと、入力欄・送信テキストにウェイクワードが
        # そのまま混入してしまう(このバグ自体はUI画面のスクリーンショットで報告された)。
        d = detect_wake_word("Hey, C.L.A.I.R.E.、明日の東京都の天気を調べる")
        self.assertIsNotNone(d)
        self.assertEqual(d.text_after, "明日の東京都の天気を調べる")

        d2 = detect_wake_word("C.L.A.I.R.E.、今何時?")
        self.assertIsNotNone(d2)


if __name__ == "__main__":
    unittest.main()
