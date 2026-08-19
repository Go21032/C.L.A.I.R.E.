"""
tests/test_media_player.py
---------------------------
15日目①: 音声による音楽再生(ytmusicapi + Brave)。

このテストは**一度もネットワークへ出ず、ブラウザも起動しない**。
ytmusicapi.YTMusic と subprocess.Popen / レジストリ参照 / ファイルシステムを
すべてモックし、「正しい引数で呼んだか」「一致度ガードが効くか」「Braveの
パス解決の優先順位」だけを見る。

⓪(15日目ノート)の安全性監査の結論を、テストでも固定する:
  - YTMusic()は必ず引数なしで生成する(認証情報を渡さない)。
  - 検索結果の一致度が閾値未満なら再生せずSongNotFoundError。
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
for p in (SCRIPTS_DIR, TESTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import media_player  # noqa: E402


class TestMatchScore(unittest.TestCase):
    """⓪-5: 検索は常に何かを返すため、一致度で弾けることが生命線。"""

    def test_exact_title_scores_high(self):
        self.assertGreaterEqual(media_player.match_score("アイドル", "アイドル"), 0.5)

    def test_title_contained_in_query_scores_high(self):
        # 「米津玄師 Lemon」で検索して「Lemon」が返るのは正解
        self.assertGreaterEqual(media_player.match_score("米津玄師 Lemon", "Lemon"), 0.5)

    def test_unrelated_title_scores_low(self):
        self.assertLess(
            media_player.match_score("あさっての天気を教えて", "明日天気になれ - Ashita Tenkininare"),
            0.5,
        )


class TestSearchSong(unittest.TestCase):
    def test_uses_unauthenticated_client(self):
        """⓪-1の採用条件: YTMusic()は必ず引数なし(=認証情報を渡さない)で生成すること。"""
        fake_cls = mock.MagicMock()
        fake_cls.return_value.search.return_value = [
            {"title": "Lemon", "videoId": "LgSLygQdHS4", "artists": [{"name": "Kenshi Yonezu"}]}
        ]
        with mock.patch.object(media_player, "_ytmusic_class", return_value=fake_cls):
            media_player.search_song("米津玄師 Lemon")
        fake_cls.assert_called_once_with()  # ★引数なしであることを固定する

    def test_returns_song_with_top_result(self):
        fake_cls = mock.MagicMock()
        fake_cls.return_value.search.return_value = [
            {"title": "Lemon", "videoId": "LgSLygQdHS4", "artists": [{"name": "Kenshi Yonezu"}]}
        ]
        with mock.patch.object(media_player, "_ytmusic_class", return_value=fake_cls):
            song = media_player.search_song("米津玄師 Lemon")
        self.assertEqual(song, media_player.Song(title="Lemon", artist="Kenshi Yonezu", video_id="LgSLygQdHS4"))

    def test_raises_when_match_score_is_too_low(self):
        """無関係な曲が返ってきたら再生せずSongNotFoundErrorにする。"""
        fake_cls = mock.MagicMock()
        fake_cls.return_value.search.return_value = [
            {"title": "時間がなくなってしまいましたが、聞いていただきありがとうございました",
             "videoId": "j7Pdpcmr-eo", "artists": []}
        ]
        with mock.patch.object(media_player, "_ytmusic_class", return_value=fake_cls):
            with self.assertRaises(media_player.SongNotFoundError):
                media_player.search_song("存在しないであろう架空の曲名ZZZQQQ12345")

    def test_raises_when_search_returns_nothing(self):
        fake_cls = mock.MagicMock()
        fake_cls.return_value.search.return_value = []
        with mock.patch.object(media_player, "_ytmusic_class", return_value=fake_cls):
            with self.assertRaises(media_player.SongNotFoundError):
                media_player.search_song("何かの曲")


class TestPlaySong(unittest.TestCase):
    def test_opens_watch_url_with_brave(self):
        with mock.patch.object(media_player, "resolve_brave_path", return_value="C:/brave.exe"), \
             mock.patch.object(media_player, "search_song", return_value=media_player.Song(
                 title="Lemon", artist="Kenshi Yonezu", video_id="LgSLygQdHS4")), \
             mock.patch("subprocess.Popen") as popen:
            result = media_player.play_song("米津玄師 Lemon")
        popen.assert_called_once_with(
            ["C:/brave.exe", "https://www.youtube.com/watch?v=LgSLygQdHS4"]
        )
        self.assertEqual(result.title, "Lemon")

    def test_raises_friendly_error_when_brave_is_missing(self):
        with mock.patch.object(media_player, "resolve_brave_path", side_effect=media_player.BraveNotFoundError("...")):
            with self.assertRaises(media_player.BraveNotFoundError):
                media_player.play_song("何か")


class TestExtractSongName(unittest.TestCase):
    def test_strips_request_verb(self):
        self.assertEqual(media_player.extract_song_name("米津玄師のLemonを流して"), "米津玄師 Lemon")

    def test_handles_various_verbs(self):
        for utterance in ["アイドルをかけて", "アイドルを再生して", "アイドルを流して"]:
            self.assertEqual(media_player.extract_song_name(utterance), "アイドル")

    def test_handles_watashite_and_kikasete(self):
        self.assertEqual(media_player.extract_song_name("マリーゴールドを聴かせて"), "マリーゴールド")
        self.assertEqual(media_player.extract_song_name("マリーゴールドを聞かせて"), "マリーゴールド")


class TestResolveBravePath(unittest.TestCase):
    """15日目⓪-6: レジストリ(App Paths)→既定インストール先→環境変数BRAVE_PATHの順。"""

    def test_uses_registry_path_when_present(self):
        with mock.patch.object(
            media_player, "_brave_path_from_registry",
            return_value=r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        ), mock.patch("pathlib.Path.exists", return_value=True):
            self.assertEqual(
                media_player.resolve_brave_path(),
                r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            )

    def test_falls_back_to_default_path_when_registry_lookup_fails(self):
        with mock.patch.object(media_player, "_brave_path_from_registry", return_value=None), \
             mock.patch("pathlib.Path.exists", return_value=True):
            # レジストリが見つからなければ、既定パスのうち最初に存在するものを使う。
            self.assertEqual(media_player.resolve_brave_path(), media_player._DEFAULT_BRAVE_PATHS[0])

    def test_falls_back_to_env_var_when_nothing_else_found(self):
        with mock.patch.object(media_player, "_brave_path_from_registry", return_value=None), \
             mock.patch(
                 "pathlib.Path.exists",
                 side_effect=[False] * len(media_player._DEFAULT_BRAVE_PATHS) + [True],
             ), \
             mock.patch.dict(os.environ, {"BRAVE_PATH": "D:/custom/brave.exe"}):
            self.assertEqual(media_player.resolve_brave_path(), "D:/custom/brave.exe")

    def test_raises_brave_not_found_error_when_nothing_matches(self):
        with mock.patch.object(media_player, "_brave_path_from_registry", return_value=None), \
             mock.patch("pathlib.Path.exists", return_value=False), \
             mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(media_player.BraveNotFoundError):
                media_player.resolve_brave_path()


if __name__ == "__main__":
    unittest.main()
