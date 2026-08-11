"""
tests/test_stt_engine.py
--------------------------
9日目ノート⑥:`stt_engine.STTEngine` のユニットテスト。

②③で確定したSTT構成(暫定表示=Vosk small-ja、確定=faster-whisper small +
`initial_prompt`)をラップする部品。実物のVosk/faster-whisperをロードすると
モデルダウンロード・数百MBのメモリを要するため、ユニットテストでは
`tests.fakes.ScriptedVoskRecognizer`と`transcribe_final`のフェイク関数を注入し、
ロジック(暫定/確定コールバックの発火・バッファ管理・辞書補正)だけを検証する。
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

from fakes import ScriptedVoskRecognizer  # noqa: E402
from stt_engine import STTEngine, correct_zunko  # noqa: E402


def make_engine(script, transcribe_final=None, **kwargs) -> tuple[STTEngine, list, list]:
    partials: list[str] = []
    finals: list[str] = []
    engine = STTEngine(
        recognizer=ScriptedVoskRecognizer(script),
        transcribe_final=transcribe_final or (lambda pcm: "(dummy)"),
        on_partial=partials.append,
        on_final=finals.append,
        **kwargs,
    )
    return engine, partials, finals


class TestPartialCallback(unittest.TestCase):
    def test_partial_callback_fires_on_change(self):
        engine, partials, _ = make_engine(
            [(False, "こん", ""), (False, "こんにちは", "")]
        )

        engine.feed_audio(b"\x00\x01")
        engine.feed_audio(b"\x00\x02")

        self.assertEqual(partials, ["こん", "こんにちは"])

    def test_partial_callback_not_called_when_unchanged(self):
        engine, partials, _ = make_engine(
            [(False, "こんにちは", ""), (False, "こんにちは", "")]
        )

        engine.feed_audio(b"\x00\x01")
        engine.feed_audio(b"\x00\x02")

        self.assertEqual(partials, ["こんにちは"])


class TestFinalCallback(unittest.TestCase):
    def test_final_callback_fires_when_vad_signals_speech_end(self):
        seen_pcm: list[bytes] = []

        def transcribe_final(pcm: bytes) -> str:
            seen_pcm.append(pcm)
            return "こんにちは、クレアです。"

        engine, _, finals = make_engine(
            [(False, "こんにちは", ""), (True, "", "こんにちは")],
            transcribe_final=transcribe_final,
        )

        engine.feed_audio(b"\x00\x01")
        engine.feed_audio(b"\x00\x02")

        self.assertEqual(finals, ["こんにちは、クレアです。"])
        # 確定転写には発話区間ぶんのPCM(両チャンク分)が渡っていること
        self.assertEqual(seen_pcm, [b"\x00\x01\x00\x02"])

    def test_no_final_when_speech_never_started(self):
        """発話がまだ始まっていない無音区間でVoskのエンドポインタが区切っても、
        stt_engine側は確定転写を走らせない(=無駄なWhisper呼び出しをしない)。"""
        engine, _, finals = make_engine([(True, "", "")])

        engine.feed_audio(b"\x00\x00")

        self.assertEqual(finals, [])

    def test_buffer_resets_after_finalize(self):
        seen_pcm: list[bytes] = []
        engine, _, finals = make_engine(
            [
                (False, "こんにちは", ""),
                (True, "", "こんにちは"),
                (False, "また", ""),
                (True, "", "また明日"),
            ],
            transcribe_final=lambda pcm: seen_pcm.append(pcm) or "OK",
        )

        for chunk in [b"\x01", b"\x02", b"\x03", b"\x04"]:
            engine.feed_audio(chunk)

        self.assertEqual(len(finals), 2)
        # 2回目の確定転写には1回目のPCMが混じっていない(バッファがリセットされている)
        self.assertEqual(seen_pcm, [b"\x01\x02", b"\x03\x04"])


class TestDictionaryCorrection(unittest.TestCase):
    def test_final_text_is_corrected_before_callback(self):
        engine, _, finals = make_engine(
            [(False, "とうほくずんこ", ""), (True, "", "とうほくずんこ")],
            transcribe_final=lambda pcm: "今日は東北銃口が担当です。",
        )

        engine.feed_audio(b"\x01")
        engine.feed_audio(b"\x02")

        self.assertEqual(finals, ["今日は東北ずん子が担当です。"])

    def test_correct_zunko_replaces_known_variants(self):
        self.assertEqual(correct_zunko("東北図んこ"), "東北ずん子")
        self.assertEqual(correct_zunko("東北順庫です"), "東北ずん子です")
        self.assertEqual(correct_zunko("東北ズンコ"), "東北ずん子")
        self.assertEqual(correct_zunko("問題ありません"), "問題ありません")


class TestFlush(unittest.TestCase):
    def test_flush_forces_final_when_buffer_pending(self):
        engine, _, finals = make_engine(
            [(False, "こんにちは", "")],
            transcribe_final=lambda pcm: "こんにちは",
        )
        engine.feed_audio(b"\x01")
        self.assertEqual(finals, [])  # まだVADが発話終了を検知していない

        engine.flush()

        self.assertEqual(finals, ["こんにちは"])

    def test_flush_is_noop_when_nothing_buffered(self):
        engine, _, finals = make_engine([])

        engine.flush()

        self.assertEqual(finals, [])


if __name__ == "__main__":
    unittest.main()
