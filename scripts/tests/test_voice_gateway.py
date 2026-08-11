"""
tests/test_voice_gateway.py
------------------------------
9日目ノート⑥:`voice_gateway.run_turn()` のユニットテスト。

`run_turn()`はFastAPI/WebSocketに依存しない「1ターン分の会話オーケストレーション」の
純粋なジェネレータ(`support_ai_auto_pipe.Pipe.pipe()`を呼び、④の`generate_stream()`が
返すトークン列を⑤の`sentence_splitter`で文に切り、1文ずつTTSへ回してWSメッセージの
形で返す)。WebSocket自体・実際のPipe/TTSはここでは検証しない
(実機確認は本ノート⑥残課題)。ここでは「トークン→文→音声」のオーケストレーションと
エラー時にUIが無反応にならないことをフェイクで検証する。
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

from voice_gateway import run_turn  # noqa: E402


class FakePipe:
    """support_ai_auto_pipe.Pipe互換の最小フェイク。pipe()の戻り値を差し替えるだけ。"""

    def __init__(self, reply):
        self._reply = reply
        self.calls: list[dict] = []

    def pipe(self, body, __user__=None, __metadata__=None, **_):
        self.calls.append({"body": body, "metadata": __metadata__})
        return self._reply


def fake_synthesize_factory(fail_on: set[str] | None = None):
    calls: list[str] = []
    fail_on = fail_on or set()

    def synthesize(text: str) -> bytes:
        calls.append(text)
        if text in fail_on:
            raise RuntimeError(f"simulated TTS failure for: {text}")
        return f"WAV({text})".encode("utf-8")

    return synthesize, calls


class TestStreamingReply(unittest.TestCase):
    def test_tokens_are_split_into_sentences_and_synthesized_in_order(self):
        # SentenceSplitterの既定min_chars=10だと短文は次の文と結合される
        # (sentence_splitter.py側の意図した仕様。tests/test_sentence_splitter.pyでも
        # 単独の分割挙動を見たいテストではmin_chars=0にしている)。
        # ここではrun_turn()の「トークン→文→音声」の順序そのものを見たいので同様に無効化する。
        tokens = iter(["こんにち", "はとても", "元気です。", "元気ですかとても"])
        pipe = FakePipe(tokens)
        synthesize, synth_calls = fake_synthesize_factory()

        events = list(
            run_turn(
                pipe, "chat-1", "こんにちは", synthesize=synthesize, splitter_kwargs={"min_chars": 0}
            )
        )

        types = [e["type"] for e in events]
        self.assertEqual(types[0], "state")
        self.assertEqual(events[0]["value"], "thinking")
        self.assertIn("token", types)
        sentence_events = [e for e in events if e["type"] == "sentence"]
        self.assertEqual(
            [e["text"] for e in sentence_events], ["こんにちはとても元気です。", "元気ですかとても"]
        )
        audio_events = [e for e in events if e["type"] == "audio"]
        self.assertEqual(
            [e["text"] for e in audio_events], ["こんにちはとても元気です。", "元気ですかとても"]
        )
        self.assertEqual(audio_events[0]["wav_b64"], _b64("WAV(こんにちはとても元気です。)"))
        self.assertEqual(types[-1], "state")
        self.assertEqual(events[-1]["value"], "idle")
        # 合成は文が確定した順に呼ばれる(=最初の文の再生が早く始まる、8日目の1分問題の解消策)
        self.assertEqual(synth_calls, ["こんにちはとても元気です。", "元気ですかとても"])

    def test_pipe_is_called_with_streaming_flag_and_chat_id(self):
        pipe = FakePipe(iter(["はい。"]))
        synthesize, _ = fake_synthesize_factory()

        list(run_turn(pipe, "chat-42", "test", synthesize=synthesize))

        call = pipe.calls[0]
        self.assertEqual(call["body"]["stream"], True)
        self.assertEqual(call["body"]["messages"], [{"role": "user", "content": "test"}])
        self.assertEqual(call["metadata"]["chat_id"], "chat-42")


class TestNonStreamingReply(unittest.TestCase):
    def test_plain_string_reply_is_still_split_and_spoken(self):
        """CODE/CLARIFYルート等、pipe()がstrを返す経路(9日目④で意図的にストリーミング対象外)。"""
        pipe = FakePipe("承知しましたので、これから実行します。準備ができました。")
        synthesize, synth_calls = fake_synthesize_factory()

        events = list(
            run_turn(pipe, "chat-1", "実行して", synthesize=synthesize, splitter_kwargs={"min_chars": 0})
        )

        sentence_events = [e["text"] for e in events if e["type"] == "sentence"]
        self.assertEqual(sentence_events, ["承知しましたので、これから実行します。", "準備ができました。"])
        self.assertEqual(synth_calls, ["承知しましたので、これから実行します。", "準備ができました。"])


class TestErrorHandling(unittest.TestCase):
    def test_pipe_exception_yields_error_event_and_still_reaches_idle(self):
        class ExplodingPipe:
            def pipe(self, body, __user__=None, __metadata__=None, **_):
                raise RuntimeError("boom")

        synthesize, synth_calls = fake_synthesize_factory()

        events = list(run_turn(ExplodingPipe(), "chat-1", "test", synthesize=synthesize))

        self.assertTrue(any(e["type"] == "error" for e in events))
        self.assertEqual(events[-1], {"type": "state", "value": "idle"})
        self.assertEqual(synth_calls, [])

    def test_tts_failure_on_one_sentence_does_not_stop_the_others(self):
        pipe = FakePipe(iter(["これはだめな文です。", "こちらは大丈夫な文です。"]))
        synthesize, synth_calls = fake_synthesize_factory(fail_on={"これはだめな文です。"})

        events = list(
            run_turn(pipe, "chat-1", "test", synthesize=synthesize, splitter_kwargs={"min_chars": 0})
        )

        error_events = [e for e in events if e["type"] == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertEqual(error_events[0]["stage"], "tts")
        audio_events = [e["text"] for e in events if e["type"] == "audio"]
        self.assertEqual(audio_events, ["こちらは大丈夫な文です。"])
        self.assertEqual(events[-1], {"type": "state", "value": "idle"})


def _b64(s: str) -> str:
    import base64

    return base64.b64encode(s.encode("utf-8")).decode("ascii")


if __name__ == "__main__":
    unittest.main()
