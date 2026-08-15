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
from stt_engine import STTEngine, correct_zunko, pcm16_bytes_to_waveform  # noqa: E402
from vad import VoskEndpointVAD  # noqa: E402

# 13日目①でVoskEndpointVADの既定silence_hold_secが3.0になったため、
# STTEngine自体のロジック(バッファ/コールバック配線)だけを見たいテストでは
# hold=0(=旧来の即時確定挙動)を明示して使う。hold自体の挙動はtest_vad.py側で検証する。
# (VADはミュータブルなので、テストごとに新しいインスタンスを作る)
def _immediate_vad_kwargs() -> dict:
    return {"vad": VoskEndpointVAD(silence_hold_sec=0)}


def make_engine(script, transcribe_final=None, **kwargs) -> tuple[STTEngine, list, list]:
    partials: list[str] = []
    finals: list[str] = []
    kwargs = {**_immediate_vad_kwargs(), **kwargs}
    engine = STTEngine(
        recognizer=ScriptedVoskRecognizer(script),
        transcribe_final=transcribe_final or (lambda pcm: "(dummy)"),
        on_partial=partials.append,
        on_final=finals.append,
        **kwargs,
    )
    return engine, partials, finals


def make_engine_with_error(script, transcribe_final=None, correct_final=None, **kwargs):
    finals: list[str] = []
    errors: list[str] = []
    kwargs = {**_immediate_vad_kwargs(), **kwargs}
    engine = STTEngine(
        recognizer=ScriptedVoskRecognizer(script),
        transcribe_final=transcribe_final or (lambda pcm: "(dummy)"),
        correct_final=correct_final if correct_final is not None else (lambda t: t),
        on_final=finals.append,
        on_error=errors.append,
        **kwargs,
    )
    return engine, finals, errors


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


class TestTranscriptionErrorHandling(unittest.TestCase):
    """実機確認(2026-08-11)で踏んだ実バグの再現テスト:create_default_engineの
    transcribe_finalがfaster-whisperにPythonのlistを渡してValueErrorになり、
    それがSTTEngine.feed_audio()から無catchで外へ伝播してWebSocketごと落ちていた
    (ブラウザ側は「切断されました」表示のままマイクが使えなくなる)。
    transcribe_final/correct_finalの失敗はon_errorへ回し、接続を落とさないこと。"""

    def test_transcribe_final_exception_calls_on_error_not_on_final(self):
        def failing_transcribe(pcm: bytes) -> str:
            raise ValueError("File object has no read() method")

        engine, finals, errors = make_engine_with_error(
            [(False, "こんにちは", ""), (True, "", "こんにちは")],
            transcribe_final=failing_transcribe,
        )

        engine.feed_audio(b"\x01")
        engine.feed_audio(b"\x02")

        self.assertEqual(finals, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("ValueError", errors[0])

    def test_correct_final_exception_calls_on_error_not_on_final(self):
        def failing_correct(text: str) -> str:
            raise RuntimeError("dictionary correction blew up")

        engine, finals, errors = make_engine_with_error(
            [(False, "こんにちは", ""), (True, "", "こんにちは")],
            transcribe_final=lambda pcm: "こんにちは",
            correct_final=failing_correct,
        )

        engine.feed_audio(b"\x01")
        engine.feed_audio(b"\x02")

        self.assertEqual(finals, [])
        self.assertEqual(len(errors), 1)

    def test_engine_recovers_for_the_next_utterance_after_an_error(self):
        """1回の確定転写が失敗しても、バッファ/VAD状態はリセットされ、
        次の発話は正常に処理できること(接続を保つための最重要条件)。"""
        calls = {"n": 0}

        def sometimes_failing_transcribe(pcm: bytes) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("boom")
            return "2回目は成功"

        engine, finals, errors = make_engine_with_error(
            [
                (False, "1回目", ""),
                (True, "", "1回目"),
                (False, "2回目", ""),
                (True, "", "2回目"),
            ],
            transcribe_final=sometimes_failing_transcribe,
        )

        for chunk in [b"\x01", b"\x02", b"\x03", b"\x04"]:
            engine.feed_audio(chunk)

        self.assertEqual(len(errors), 1)
        self.assertEqual(finals, ["2回目は成功"])


class TestForceFinalizePending(unittest.TestCase):
    """19日目 修正:`force_finalize_pending()`のユニットテスト。

    ウェイクワード検出直後にVADの無音保持(silence_hold_sec)を待たず確定させる部品。
    実機で報告されたバグ(ウェイクワード発話「クレア起動」と3秒未満のポーズで続く
    コマンド発話が1つの確定テキストに連結されてしまう)の根本原因は、
    `vad.VoskEndpointVAD`の「保留中に発話が再開したら区切りを取り消す」仕様と、
    ウェイクワード検出後すぐに次の発話が来る典型的な使い方が噛み合っていなかったこと。
    """

    def test_forces_final_without_waiting_for_silence_hold(self):
        """silence_hold_sec中(まだhold時間が経過していない)でも、その場で確定する。"""
        engine, _, finals = make_engine(
            [(False, "クレア起動", ""), (True, "", "クレア起動")],
            transcribe_final=lambda pcm: "クレア起動",
            vad=VoskEndpointVAD(silence_hold_sec=3.0),  # holdありの本来の既定値で検証
        )

        engine.feed_audio(b"\x01")  # on_partialで「クレア起動」を検出した想定
        engine.feed_audio(b"\x02")  # Voskのエンドポイント検出(acceptedだがholdでpending中)
        self.assertEqual(finals, [])  # まだ3秒経っていないので通常なら確定しない

        engine.force_finalize_pending()

        self.assertEqual(finals, ["クレア起動"])

    def test_resets_the_underlying_recognizer(self):
        """Kaldi側のデコード状態もResetし、以降のpartialにウェイクワードの文字が
        残らないようにする(暫定プレビューへの混入防止)。"""
        engine, _, _ = make_engine(
            [(False, "クレア起動", "")], transcribe_final=lambda pcm: "クレア起動"
        )
        recognizer: ScriptedVoskRecognizer = engine.recognizer

        engine.feed_audio(b"\x01")
        self.assertEqual(recognizer.reset_calls, 0)

        engine.force_finalize_pending()

        self.assertEqual(recognizer.reset_calls, 1)

    def test_buffer_and_vad_are_clean_for_the_next_utterance(self):
        """force_finalize_pending()後、次に来る音声は前の発話と混ざらない
        (①バッファがリセットされる、②その後のVAD状態が新しい発話として扱われる)。"""
        seen_pcm: list[bytes] = []
        engine, _, finals = make_engine(
            [
                (False, "クレア起動", ""),
                (False, "明日の天気は", ""),
                (True, "", "明日の天気は"),
            ],
            transcribe_final=lambda pcm: seen_pcm.append(pcm) or "dummy",
        )

        engine.feed_audio(b"\x01")  # 「クレア起動」検出
        engine.force_finalize_pending()  # ここで区切る(ウェイクワード検出直後を模擬)
        seen_pcm.clear()
        finals.clear()

        engine.feed_audio(b"\x02")  # 続くコマンド発話(新しい発話として処理されるべき)
        engine.feed_audio(b"\x03")

        self.assertEqual(len(finals), 1)
        # 「クレア起動」チャンク(\x01)が混ざっていないこと
        self.assertEqual(seen_pcm, [b"\x02\x03"])

    def test_noop_when_nothing_is_buffered(self):
        engine, _, finals = make_engine([])

        engine.force_finalize_pending()

        self.assertEqual(finals, [])


class TestPcm16ToWaveform(unittest.TestCase):
    """③実機比較結果⑥・9日目⑥実機確認(2026-08-11)で踏んだバグの根本原因:
    faster-whisperのWhisperModel.transcribe()はaudioにndarrayを渡さないと
    (`isinstance(audio, np.ndarray)`がFalseだと)decode_audio()経由でファイルとして
    開こうとして失敗する。plain listを渡していたのが実機での認識精度低下
    (Whisperが一度も成功せず、Vosk暫定表示が最終結果扱いのまま残っていた)の原因だった。"""

    def test_converts_pcm16_bytes_to_float32_numpy_array(self):
        import struct

        import numpy as np

        pcm = struct.pack("<3h", 0, 16384, -32768)  # 0.0, 0.5, -1.0 相当

        waveform = pcm16_bytes_to_waveform(pcm)

        self.assertIsInstance(waveform, np.ndarray)
        self.assertEqual(waveform.dtype, np.float32)
        self.assertEqual(len(waveform), 3)
        self.assertAlmostEqual(waveform[0], 0.0, places=4)
        self.assertAlmostEqual(waveform[1], 16384 / 32768.0, places=4)
        self.assertAlmostEqual(waveform[2], -1.0, places=4)

    def test_empty_input_returns_empty_array(self):
        import numpy as np

        waveform = pcm16_bytes_to_waveform(b"")

        self.assertIsInstance(waveform, np.ndarray)
        self.assertEqual(len(waveform), 0)


if __name__ == "__main__":
    unittest.main()
