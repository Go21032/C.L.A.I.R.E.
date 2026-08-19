"""media_player.py — 15日目①: 音声からの音楽再生(曲名 → Braveで YouTube 再生)。

設計上の約束(15日目⓪の安全性監査の結論。緩めないこと):
  - ytmusicapiは**認証なし**(`YTMusic()`を引数なしで生成)で**検索にのみ**使う。
    Googleアカウントの情報は一切渡さない。外へ出るのは曲名の検索文字列だけ。
  - 音源の**ダウンロードは行わない**(利用規約違反)。再生は通常どおり
    Braveで youtube.com の視聴ページを開いて行う。
  - バージョンは`ytmusicapi==1.12.2`にピン留めする。上げるときは
    15日目⓪と同じ監査(バイナリ/危険呼び出し/通信先/書き込み箇所の検分)を再実行する。
  - **LLMからは呼ばせない**。code_executorのライブラリ・ホワイトリストに
    このモジュールと`ytmusicapi`を入れないこと(14日目④のgoogle_workspaceと同じ)。
"""

from __future__ import annotations

import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

WATCH_URL = "https://www.youtube.com/watch?v={video_id}"

# 15日目⓪-5: 検索は「見つからない」を返さず必ず何かを返すため、一致度で弾く。
# 実測では正解が1.00、無関係が0.11〜0.14と大きく開いたので0.5で安全に切れる。
MATCH_THRESHOLD = 0.5

# 「〜を流して/かけて/再生して」から曲名部分だけを切り出す
_REQUEST_SUFFIX = re.compile(r"[をの]?\s*(流して|かけて|再生して|聴かせて|聞かせて).*$")

# 15日目⓪-6: レジストリのApp Pathsで実測確認済みのキー(HKLM\...\App Paths\brave.exe)。
_BRAVE_APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\brave.exe"

# レジストリ・環境変数どちらでも見つからない場合の最終フォールバック(既定インストール先)。
_DEFAULT_BRAVE_PATHS: list[str] = [
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
]


class MediaPlayerError(RuntimeError):
    """UIへそのまま見せられる日本語メッセージを持たせる基底例外。"""


class SongNotFoundError(MediaPlayerError):
    """一致する曲が見つからなかった(⓪-5のガードで弾いた場合を含む)。"""


class BraveNotFoundError(MediaPlayerError):
    """Braveの実行ファイルが見つからない。"""


@dataclass(frozen=True)
class Song:
    title: str
    artist: str
    video_id: str

    @property
    def url(self) -> str:
        return WATCH_URL.format(video_id=self.video_id)


def normalize(text: str) -> str:
    """全角/半角・大小文字・空白の差を吸収する(wake_word.pyと同じ考え方)。"""
    return unicodedata.normalize("NFKC", text).lower().replace(" ", "").replace("　", "")


def match_score(query: str, title: str) -> float:
    """問い合わせとヒットしたタイトルの一致度。片方が他方を含むなら1.0扱い。"""
    q, t = normalize(query), normalize(title)
    if t and (t in q or q in t):
        return 1.0
    return SequenceMatcher(None, q, t).ratio()


def extract_song_name(utterance: str) -> str:
    """「米津玄師のLemonを流して」→「米津玄師 Lemon」。

    まず依頼動詞(流して/かけて/再生して/聴かせて/聞かせて)以降を切り落とし、
    残った文字列の最初の「の」だけを空白に置き換えて「アーティスト 曲名」の
    形に寄せる(ytmusicapiの検索は空白区切りの複合語でも精度が良いことを
    15日目⓪-4で実測済み)。
    """
    stripped = _REQUEST_SUFFIX.sub("", utterance).strip()
    return stripped.replace("の", " ", 1)


def _brave_path_from_registry() -> str | None:
    """HKLMのApp Pathsからbrave.exeの場所を引く(15日目⓪-6で実測確認済み)。

    winregはWindows専用のためここで初めてimportする。キーが無い環境
    (未インストール・非Windows)ではNoneを返し、呼び出し側でフォールバックする。
    """
    import winreg  # noqa: PLC0415

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _BRAVE_APP_PATHS_KEY) as key:
            value, _ = winreg.QueryValueEx(key, None)
            return value
    except OSError:
        return None


def resolve_brave_path() -> str:
    """Braveの実行ファイルを探す。レジストリ→既定インストール先→環境変数`BRAVE_PATH`の順。

    15日目⓪-6の実測ではHKLMの App Paths に登録されていたが、環境差や
    アップデートで変わりうるためハードコードしない。どれも見つからなければ
    例外を投げず黙って失敗させず、BraveNotFoundErrorとして明示的に伝える。
    """
    candidates = [_brave_path_from_registry(), *_DEFAULT_BRAVE_PATHS, os.environ.get("BRAVE_PATH")]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise BraveNotFoundError(
        "Braveが見つかりません。BraveSoftwareをインストールするか、"
        "環境変数BRAVE_PATHにbrave.exeのパスを設定してください。"
    )


def _ytmusic_class():
    """ytmusicapiをここで初めてimportする(遅延import)。

    未インストール環境でもこのモジュール自体のimportでは落ちないようにするため
    (voice_gateway.py全体が道連れで起動失敗しないようにするための約束。作業内容参照)。
    呼び出し側は返ってきたクラスを**必ず引数なし**で生成すること(⓪の採用条件1)。
    """
    from ytmusicapi import YTMusic  # noqa: PLC0415

    return YTMusic


def search_song(query: str) -> Song:
    """曲名から動画IDを解決する。一致度が閾値未満ならSongNotFoundError。"""
    ytmusic = _ytmusic_class()()  # ★引数なし = 認証なし(採用条件1)
    results = ytmusic.search(query, filter="songs", limit=1)
    if not results:
        raise SongNotFoundError(f"「{query}」という曲が見つかりませんでした。")

    top = results[0]
    title = top.get("title", "")
    if match_score(query, title) < MATCH_THRESHOLD:
        # ⓪-5: 検索は常に何かを返すため、一致度で弾かないと聞き間違いで
        # 無関係な曲が再生されてしまう。
        raise SongNotFoundError(f"「{query}」という曲が見つかりませんでした。")

    artists = top.get("artists") or []
    artist = artists[0]["name"] if artists else ""
    return Song(title=title, artist=artist, video_id=top["videoId"])


def play_song(query: str) -> Song:
    """曲を検索し、BraveでYouTubeの視聴ページを開いて再生する。"""
    brave = resolve_brave_path()
    song = search_song(query)
    subprocess.Popen([brave, song.url])
    return song
