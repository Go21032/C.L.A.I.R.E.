"""
tests/test_ws_e2e_bench.py
-----------------------------
11日目ノート①ステップ5自動化(`ws_e2e_bench.py`)の統合テスト。

実際のVosk/faster-whisper/Ollama/VOICEVOXは一切使わず、`voice_gateway.create_app()`の
差し替えポイント(`pipe_factory` / `stt_engine_factory` / `synthesize`)をフェイクに
差し替えたFastAPIアプリを、実際にuvicornでlocalhostの空きポートへ立ち上げる
(`ws_e2e_bench.py`は本物のWebSocket接続を張るため、TestClientの疑似WebSocketでは
代替できない)。これにより:

  - `ws_e2e_bench.load_pcm16_16k_mono()` / `chunk_pcm()` が実物のwav
    (results/stt_bench/sample01.wav、24kHz/mono)を16kHz PCMへ正しく変換できるか
  - `ws_e2e_bench.run_one()` が音声チャンクをストリーミングし、
    `{"type":"partial_transcript","final":true}` を検知して
    `seg_speech_end_to_stt_final` を計算できるか
  - STT確定後に自動で`text_input`を送り、`run_turn()`完了(state:idle)まで
    追跡できるか(2026-08-12改修: voice_gateway.py側の"final"フラグ追加とセット)

を、外部サービス無しで検証する。
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
for p in (SCRIPTS_DIR, TESTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import uvicorn  # noqa: E402

import ws_e2e_bench  # noqa: E402
from voice_gateway import create_app  # noqa: E402

SAMPLE_AUDIO = SCRIPTS_DIR / "results" / "stt_bench" / "sample01.wav"
TEST_PORT = 18765


class FakePipe:
    """support_ai_auto_pipe.Pipe互換の最小フェイク(tests/test_voice_gateway.pyと同型)。"""

    def pipe(self, body, __user__=None, __metadata__=None, **_):
        return "テスト応答です。これでrun_turnが完了します。"


class FakeSTTEngine:
    """stt_engine.STTEngine互換の最小フェイク。

    voice_gateway.ws_endpointからは`feed_audio(bytes)`/`flush()`だけを叩かれる。
    実際のVAD/Voskの代わりに、「無音チャンクが一定数続いたら発話終了とみなす」
    単純なルールでon_final()を呼ぶ(本物のVoskEndpointVADの挙動を模した簡易版)。
    """

    def __init__(self, on_partial, on_final, on_error, *, silence_chunks_to_final: int = 5):
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_error = on_error
        self._silence_run = 0
        self._silence_threshold = silence_chunks_to_final
        self._finalized = False
        self.feed_count = 0

    def feed_audio(self, chunk: bytes) -> None:
        self.feed_count += 1
        if self._finalized:
            return
        is_silence = chunk == b"\x00" * len(chunk)
        self._on_partial("にんしきちゅう" if not is_silence else "")
        if is_silence:
            self._silence_run += 1
        else:
            self._silence_run = 0
        if is_silence and self._silence_run >= self._silence_threshold:
            self._finalized = True
            self._on_final("こんにちはテストです")

    def flush(self) -> None:
        if not self._finalized:
            self._finalized = True
            self._on_final("こんにちはテストです")


def _fake_synthesize(text: str) -> bytes:
    return f"WAV({text})".encode("utf-8")


def _build_test_app():
    return create_app(
        pipe_factory=lambda: FakePipe(),
        stt_engine_factory=lambda on_partial, on_final, on_error: FakeSTTEngine(
            on_partial, on_final, on_error
        ),
        synthesize=_fake_synthesize,
    )


class WsE2EBenchIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SAMPLE_AUDIO.exists():
            raise unittest.SkipTest(f"サンプル音声が無いためスキップ: {SAMPLE_AUDIO}")

        app = _build_test_app()
        config = uvicorn.Config(app, host="127.0.0.1", port=TEST_PORT, log_level="warning")
        cls.server = uvicorn.Server(config)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()

        deadline = time.monotonic() + 10.0
        while not cls.server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not cls.server.started:
            raise RuntimeError("テスト用uvicornサーバの起動確認がタイムアウトしました")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=5)

    def test_load_pcm16_16k_mono_converts_sample_rate_and_duration(self):
        pcm = ws_e2e_bench.load_pcm16_16k_mono(SAMPLE_AUDIO)
        seconds = len(pcm) / 2 / ws_e2e_bench.TARGET_SAMPLE_RATE
        # sample01.wavは24kHz/7.712秒(9日目③実機比較結果⑥で使用したサンプル)。
        # 16kHzへの変換後もおおよそ同じ長さになるはず。
        self.assertAlmostEqual(seconds, 7.712, delta=0.05)

    def test_run_one_detects_stt_final_and_completes_turn_via_fake_stack(self):
        run = asyncio.run(
            ws_e2e_bench.run_one(
                f"ws://127.0.0.1:{TEST_PORT}/ws",
                SAMPLE_AUDIO,
                trailing_silence_ms=1000,
                stt_timeout_s=5.0,
                turn_timeout_s=10.0,
            )
        )

        self.assertIsNone(run.error, msg=run.error)
        self.assertEqual(run.stt_text, "こんにちはテストです")

        t_speech_end = run.timings.get("t_speech_end")
        t_stt_final = run.timings.get("t_stt_final")
        self.assertIsNotNone(t_speech_end)
        self.assertIsNotNone(t_stt_final)
        self.assertGreaterEqual(t_stt_final, t_speech_end)

        seg = run.timings.get("seg_speech_end_to_stt_final")
        self.assertEqual(seg, t_stt_final - t_speech_end)
        self.assertGreaterEqual(seg, 0.0)

        # 自動継続(text_input送信)でrun_turn()がstate:idleまで完了していること
        self.assertIn("t_end", run.timings)
        self.assertNotIn("turn_timeout", run.timings)

        # partial_transcript(final=false)とfinal=trueの両方が実際のWS越しに届いていること
        types_final_flags = [
            (m.get("type"), m.get("final"))
            for m in run.messages
            if m.get("type") == "partial_transcript"
        ]
        self.assertIn(("partial_transcript", True), types_final_flags)
        self.assertTrue(any(f is False for _, f in types_final_flags))

    def test_no_auto_continue_stops_after_stt_final(self):
        run = asyncio.run(
            ws_e2e_bench.run_one(
                f"ws://127.0.0.1:{TEST_PORT}/ws",
                SAMPLE_AUDIO,
                trailing_silence_ms=1000,
                stt_timeout_s=5.0,
                auto_continue=False,
            )
        )

        self.assertIsNone(run.error, msg=run.error)
        self.assertEqual(run.stt_text, "こんにちはテストです")
        self.assertNotIn("t_end", run.timings)


if __name__ == "__main__":
    unittest.main()
