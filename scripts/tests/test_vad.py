"""
tests/test_vad.py
--------------------
9日目ノート⑥:`vad.VoskEndpointVAD` のユニットテスト。

③の残課題「VoskのResult()タイミングを発話区切りに流用できるか」の検証部品。
silero-vad/webrtcvadのような波形そのものを見るVADとは違い、
`VoskEndpointVAD`はVoskの`AcceptWaveform()`が返す真偽値(=Kaldi内部の
エンドポインタが区切りを検出したタイミング)だけを見て「発話開始」「発話終了」の
イベントに変換する薄い層。**音声そのものは一切扱わない**ため、Vosk本体を
インストールしなくても純粋なロジックとして検証できる。
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

from vad import VadEventType, VoskEndpointVAD  # noqa: E402


class TestSpeechStart(unittest.TestCase):
    def test_speech_start_fires_on_first_nonempty_partial(self):
        vad = VoskEndpointVAD()

        events = vad.observe(accepted=False, partial_text="こん")

        self.assertEqual([e.type for e in events], [VadEventType.SPEECH_START])

    def test_no_speech_start_while_partial_stays_empty(self):
        vad = VoskEndpointVAD()

        events = vad.observe(accepted=False, partial_text="")

        self.assertEqual(events, [])

    def test_speech_start_fires_only_once_per_utterance(self):
        vad = VoskEndpointVAD()
        vad.observe(accepted=False, partial_text="こん")

        events = vad.observe(accepted=False, partial_text="こんにちは")

        self.assertEqual(events, [])


class TestSpeechEnd(unittest.TestCase):
    """13日目①でsilence_hold_secの既定値が3.0になったため、ここでは明示的に
    silence_hold_sec=0(=旧来の即時確定挙動)を指定してSPEECH_END自体のロジックを
    検証する。hold(保留)の挙動はTestSilenceHoldで別途検証する。"""

    def test_speech_end_fires_when_accepted_after_speech_started(self):
        vad = VoskEndpointVAD(silence_hold_sec=0)
        vad.observe(accepted=False, partial_text="こんにちは")

        events = vad.observe(accepted=True, final_text="こんにちは")

        self.assertEqual([e.type for e in events], [VadEventType.SPEECH_END])
        self.assertEqual(events[0].text, "こんにちは")

    def test_state_returns_to_idle_after_speech_end(self):
        vad = VoskEndpointVAD(silence_hold_sec=0)
        vad.observe(accepted=False, partial_text="こんにちは")
        vad.observe(accepted=True, final_text="こんにちは")

        self.assertEqual(vad.state.value, "idle")

    def test_accepted_true_while_idle_and_silent_is_ignored(self):
        """発話がまだ始まっていないのに(=無音が続いた末に)Voskの
        エンドポインタがたまたま区切った場合は、発話終了として扱わない。"""
        vad = VoskEndpointVAD(silence_hold_sec=0)

        events = vad.observe(accepted=True, final_text="")

        self.assertEqual(events, [])
        self.assertEqual(vad.state.value, "idle")

    def test_short_final_text_below_threshold_is_dropped(self):
        vad = VoskEndpointVAD(min_utterance_chars=3, silence_hold_sec=0)
        vad.observe(accepted=False, partial_text="あ")

        events = vad.observe(accepted=True, final_text="あ")

        self.assertEqual(events, [])
        self.assertEqual(vad.state.value, "idle")

    def test_new_speech_start_can_follow_a_completed_utterance(self):
        vad = VoskEndpointVAD(silence_hold_sec=0)
        vad.observe(accepted=False, partial_text="こんにちは")
        vad.observe(accepted=True, final_text="こんにちは")

        events = vad.observe(accepted=False, partial_text="次の発話")

        self.assertEqual([e.type for e in events], [VadEventType.SPEECH_START])


class TestReset(unittest.TestCase):
    def test_reset_returns_to_idle_and_allows_speech_start_again(self):
        vad = VoskEndpointVAD()
        vad.observe(accepted=False, partial_text="こんにちは")

        vad.reset()
        events = vad.observe(accepted=False, partial_text="こんにちは")

        self.assertEqual([e.type for e in events], [VadEventType.SPEECH_START])


class FakeClock:
    """13日目①: silence_hold_secの経過をテストで再現するための偽時計。"""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, sec: float) -> None:
        self.now += sec


class TestSilenceHold(unittest.TestCase):
    """13日目①: Voskが区切りを検出しても`silence_hold_sec`経過まではSPEECH_ENDを保留する。"""

    def test_speech_end_is_held_until_silence_hold_elapses(self):
        clock = FakeClock()
        vad = VoskEndpointVAD(silence_hold_sec=3.0, clock=clock)
        vad.observe(accepted=False, partial_text="こんにちは")
        events = vad.observe(accepted=True, final_text="こんにちは")
        self.assertEqual(events, [])

        clock.advance(2.9)
        self.assertEqual(vad.observe(accepted=False, partial_text=""), [])

        clock.advance(0.2)
        events = vad.observe(accepted=False, partial_text="")
        self.assertEqual([e.type for e in events], [VadEventType.SPEECH_END])
        self.assertEqual(events[0].text, "こんにちは")

    def test_speech_resumed_within_hold_cancels_end_and_concatenates(self):
        clock = FakeClock()
        vad = VoskEndpointVAD(silence_hold_sec=3.0, clock=clock)
        vad.observe(accepted=False, partial_text="えーっと")
        self.assertEqual(vad.observe(accepted=True, final_text="えーっと"), [])
        clock.advance(1.0)
        self.assertEqual(vad.observe(accepted=False, partial_text="明日の天気は"), [])
        clock.advance(0.5)
        events = vad.observe(accepted=True, final_text="明日の天気は")
        self.assertEqual(events, [])
        clock.advance(3.1)
        events = vad.observe(accepted=False, partial_text="")
        self.assertEqual([e.type for e in events], [VadEventType.SPEECH_END])
        self.assertEqual(events[0].text, "えーっと明日の天気は")

    def test_zero_hold_keeps_legacy_immediate_behavior(self):
        vad = VoskEndpointVAD(silence_hold_sec=0.0)
        vad.observe(accepted=False, partial_text="やあ")
        events = vad.observe(accepted=True, final_text="やあ")
        self.assertEqual([e.type for e in events], [VadEventType.SPEECH_END])


if __name__ == "__main__":
    unittest.main()
