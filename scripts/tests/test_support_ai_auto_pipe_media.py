"""
tests/test_support_ai_auto_pipe_media.py
------------------------------------------
15日目②: MEDIAルート(音楽再生)のsupport_ai_auto_pipe.py側の結線テスト。

media_playerはフェイクへ差し替え、ネットワークへ出ず実際にブラウザも起動しない。
「MEDIAルートはLLMを一切通さない(router.call_phi4を呼ばない)」ことと、
「MediaPlayerError系が日本語メッセージへ変換されること」を確認する。
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
OPENWEBUI_PIPE_DIR = SCRIPTS_DIR / "openwebui_pipe"
for p in (SCRIPTS_DIR, OPENWEBUI_PIPE_DIR, TESTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import router  # noqa: E402
import support_ai_auto_pipe  # noqa: E402
from fakes import NoopMemoryStore  # noqa: E402


def make_body(text: str, chat_id: str = "chat-1") -> dict:
    return {"chat_id": chat_id, "messages": [{"role": "user", "content": text}]}


class TestMediaRoute(unittest.TestCase):
    def setUp(self):
        self._orig_call_phi4 = router.call_phi4
        self._orig_ensure_model_ready = router.ensure_model_ready
        self._orig_memory_store = support_ai_auto_pipe.memory_store
        self._orig_generate = support_ai_auto_pipe.generate
        router.ensure_model_ready = lambda route: None
        support_ai_auto_pipe.memory_store = NoopMemoryStore()

        def fail_call_phi4(system_prompt, user_text):
            raise AssertionError("MEDIA route should not go through call_phi4/LLM classification")

        router.call_phi4 = fail_call_phi4

    def tearDown(self):
        router.call_phi4 = self._orig_call_phi4
        router.ensure_model_ready = self._orig_ensure_model_ready
        support_ai_auto_pipe.memory_store = self._orig_memory_store
        support_ai_auto_pipe.generate = self._orig_generate

    def test_play_request_plays_song_and_announces_it(self):
        pipe = support_ai_auto_pipe.Pipe()
        song = support_ai_auto_pipe.media_player.Song(
            title="Lemon", artist="Kenshi Yonezu", video_id="LgSLygQdHS4"
        )
        with mock.patch.object(support_ai_auto_pipe.media_player, "play_song", return_value=song) as fake_play:
            reply = pipe.pipe(make_body("米津玄師のLemonを流して"))
        fake_play.assert_called_once_with("米津玄師 Lemon")
        self.assertIn("Lemon", reply)
        self.assertIn("Kenshi Yonezu", reply)

    def test_play_request_records_media_played_for_ui_chip(self):
        # 15日目③: voice_gateway.pyがWSへmedia_playedイベントを送るための土台。
        pipe = support_ai_auto_pipe.Pipe()
        song = support_ai_auto_pipe.media_player.Song(
            title="Lemon", artist="Kenshi Yonezu", video_id="LgSLygQdHS4"
        )
        with mock.patch.object(support_ai_auto_pipe.media_player, "play_song", return_value=song):
            pipe.pipe(make_body("米津玄師のLemonを流して"))
        self.assertEqual(
            pipe.last_media_played,
            {"title": "Lemon", "artist": "Kenshi Yonezu", "url": song.url},
        )

    def test_song_not_found_returns_friendly_message(self):
        pipe = support_ai_auto_pipe.Pipe()
        with mock.patch.object(
            support_ai_auto_pipe.media_player,
            "play_song",
            side_effect=support_ai_auto_pipe.media_player.SongNotFoundError(
                "「存在しない曲」という曲が見つかりませんでした。"
            ),
        ):
            reply = pipe.pipe(make_body("存在しない曲を流して"))
        self.assertIn("見つかりませんでした", reply)
        self.assertIsNone(pipe.last_media_played)

    def test_brave_not_found_returns_friendly_message(self):
        pipe = support_ai_auto_pipe.Pipe()
        with mock.patch.object(
            support_ai_auto_pipe.media_player,
            "play_song",
            side_effect=support_ai_auto_pipe.media_player.BraveNotFoundError("Braveが見つかりません。"),
        ):
            reply = pipe.pipe(make_body("何かを流して"))
        self.assertIn("Braveが見つかりません", reply)
        self.assertIsNone(pipe.last_media_played)

    def test_casual_music_mention_does_not_trigger_media_route(self):
        pipe = support_ai_auto_pipe.Pipe()

        def fake_call_phi4(system_prompt, user_text):
            return '{"route": "FAST"}'

        router.call_phi4 = fake_call_phi4
        support_ai_auto_pipe.generate = lambda model, prompt, **kwargs: "音楽の話、いいですね。"
        with mock.patch.object(support_ai_auto_pipe.media_player, "play_song") as fake_play:
            reply = pipe.pipe(make_body("今日は一日音楽を聴いていたよ"))
        fake_play.assert_not_called()
        self.assertNotIn("再生します", reply)


if __name__ == "__main__":
    unittest.main()
