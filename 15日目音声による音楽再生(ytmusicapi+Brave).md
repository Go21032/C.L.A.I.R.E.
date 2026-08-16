---
project: C.L.A.I.R.E.(さぽーとAI)
date: 2026-08-16
tags: [音楽再生, ytmusicapi, YouTube, Brave, ブラウザ連携, MEDIAルート, ルーティング, 音声操作, サプライチェーン, セキュリティ監査, 作業ログ]
status: 計画(着手前)。①`media_player.py`新規(曲名→動画IDの解決と一致度ガード) ②Braveの起動とパス解決 ③MEDIAルートの新設とルーティング ④UIへの再生チップ表示
---

[[サポートAI作製計画/14日目添付ファイルのピン留め指定とチャット履歴の永続化.md|14日目]]で①ピン留めによるファイル指定・②チャット履歴の永続化・③資料生成(Excel/PowerPoint/Word/JSON)・④Google Workspace連携がすべて完了し、実機確認も全項目を通した。14日目は「AIに情報を**入れる**」「AIから成果物を**出す**」という**情報の出入り**の整備だったが、15日目の今日は方向を変えて**AIにPC上のソフトを操作させる**最初の一歩に取り組む。具体的には「**〇〇(曲名)を流して**」と話しかけると、Braveが起動してYouTubeでその曲が再生される機能を作る。

> [!note] このノートの位置づけ
> [[サポートAI作製計画/ノート作成規則.md]]に従い、**着手前に作業内容を列挙した計画ノート**として作成した。ただし⓪の安全性監査と技術検証(スパイク)は着手前に実施済みのため、⓪だけは結果を先に記載している。

> [!warning] 今日のスコープ外(意図的に触らない)
> - **停止・曲送り・音量調整**:相談の上「再生だけ」に絞った。停止や曲送りはBraveのタブ側で手動操作する。これらを実装するとYouTubeのUIへキーボードショートカットを送る必要があり、`pyautogui`等の**画面操作依存**が入って壊れやすくなるため
> - **汎用ブラウザ起動**(「Braveで〇〇を開いて」で任意サイトを開く):LLMが任意URLを開けることになり、URLホワイトリスト等の安全境界の設計が別途必要になる。今日は**音楽再生専用**に限定する
> - **ローカル音楽ファイルの再生**:手元のミュージックフォルダを走査して再生する案は有力だが、今日はYouTube側だけを通す。必要になったら16日目以降に「ローカル優先+YouTubeフォールバック」として追加する
> - **音源のダウンロード・保存**:YouTubeからの音源ダウンロードは利用規約違反であり、**実装しない**。ytmusicapiは**検索(曲名→動画IDの解決)にのみ使用**し、再生は通常どおりBraveでYouTubeのページを開いて行う
> - **プレイリスト・連続再生・「次も似た曲を」**:1回の発話で1曲。まず最小の経路を通す
> - **ytmusicapiの認証機能**(自分のライブラリ・高評価曲へのアクセス):Googleアカウント情報を渡すことになるため使わない(⓪-1の監査結論)

---

## ⓪ 着手前調査(ytmusicapiの安全性監査と技術検証)

### 背景/目的

「曲名→動画IDの解決」に何を使うかを決める必要があった。当初はGoogle公式のYouTube Data API v3を検討したが、**無料枠が10,000ユニット/日・検索1回100ユニット=1日100回**という制限があり、「制限を気にしながら使うのは避けたい」という判断から非公式ライブラリ`ytmusicapi`を候補にした。

ただし非公式ライブラリをPCへ入れる以上、**①外部へ情報が漏れないか ②ウィルス等の混入がないか ③PCのファイル・ソフト・システムを壊さないか**を着手前に確認する必要がある。14日目⓪で確立した「**着手前に実態を確認してから計画を立てる**」手順を、今回はセキュリティ監査へ適用した。

### 実施方法

**インストールせずに**ホイールをダウンロードして中身を全数検分した。

```bash
# 1. インストールせずダウンロードのみ(ホイールの展開はコードを実行しない)
cd /tmp && mkdir -p ytm_audit
python -m pip download ytmusicapi==1.12.2 --no-deps --only-binary=:all: -d /tmp/ytm_audit

# 2. ファイル構成とバイナリの有無を確認
python -c "
import zipfile, collections
z = zipfile.ZipFile('ytmusicapi-1.12.2-py3-none-any.whl')
names = z.namelist()
print('総ファイル数:', len(names))
bad = [n for n in names if n.lower().endswith(('.so','.pyd','.dll','.exe','.bat','.cmd','.sh','.ps1','.bin'))]
print('バイナリ/実行ファイル:', bad if bad else 'なし')
z.extractall('extracted')
"

# 3. 危険な呼び出しパターンの全文検索
cd extracted
grep -rnE "subprocess|os\.system|os\.popen|eval\(|exec\(|__import__|pickle|marshal|base64\.b64decode|socket\.|ctypes|shutil\.rmtree|os\.remove|os\.unlink" --include=*.py .

# 4. 通信先ドメインとファイル書き込み箇所の全列挙
grep -rhoE "https?://[a-zA-Z0-9.-]+" --include=*.py . | sort -u
grep -rnE "open\(.*[\"'][wa]|write_text|write_bytes|\.write\(" --include=*.py .
```

### 結果

**1. 静的監査:クリーン**

| 検査項目 | 結果 |
|---|---|
| ホイール形式 | `py3-none-any` = **純Python**。コンパイル済みバイナリを含まない |
| バイナリ/実行ファイル(`.so`/`.pyd`/`.dll`/`.exe`/`.bat`/`.sh`) | **0件**(全76ファイル中:`.py` 51 / `.mo` 17(翻訳ファイル) / その他8) |
| `subprocess`・`os.system`・`os.popen` | **0件** |
| `eval()`・`exec()`・`__import__` | **0件** |
| `pickle`・`marshal`・`base64.b64decode`(難読化ペイロードの常套手段) | **0件** |
| `socket`・`ctypes` | **0件** |
| ファイル削除(`os.remove`/`shutil.rmtree`) | **0件** |
| 実行時依存 | **`requests >= 2.22` のみ**(本体環境に2.34.2が導入済み)。**新規に増える依存はゼロ** |
| ライセンス | MIT |
| 既知の脆弱性(Snyk) | **なし**。ヘルススコア82/100、Maintenance: Healthy、Community: Sustainable |
| 保守状況 | GitHub 2.9k stars、最新v1.12.2は**8日前**(2026-08-08)リリース |

**2. 🔑 認証なしで検索できることをソースで確認(最重要)**

```python
# ytmusicapi/ytmusic.py:53
def __init__(self, auth: str | JsonDict | None = None, ...):
    self.auth_type = AuthType.UNAUTHORIZED
    if auth is not None:          # ← ここに入らない限り認証処理は一切動かない
```

`YTMusic()`と**引数なしで生成すれば`AuthType.UNAUTHORIZED`のまま**で、Googleアカウントの認証情報を一切渡さない。外部へ出るのは「曲名の検索文字列」だけであり、宛先は`music.youtube.com/youtubei/v1/`のみ。**ブラウザでYouTubeの検索窓に曲名を打つのと情報量は同じ**である。

**3. ファイル書き込みは認証パスにしか存在しない**

全76ファイル中、書き込みは以下の2箇所だけだった。

```
ytmusicapi/auth/browser.py:76       → browser.json の保存
ytmusicapi/auth/oauth/token.py:153  → oauth.json の保存
```

上記2はいずれも`auth/`配下であり、2の`if auth is not None:`により**認証なし運用では一度も実行されない**。つまり**認証なし運用ではファイル書き込みが1件も発生しない**。14日目④の`scripts/secrets/token.json`(Google Docs/Driveのトークン)に触れる経路も存在しない。

**4. 実動作の検証(使い捨てvenvでのスパイク)**

本体環境を汚さないよう、`/tmp`に使い捨ての仮想環境を作って検証した(検証後に削除済み)。

```bash
python -m venv /tmp/ytm_spike
/tmp/ytm_spike/Scripts/python.exe -m pip install "ytmusicapi==1.12.2"
# → 入ったのは ytmusicapi / requests / certifi / charset-normalizer / idna / urllib3 のみ
```

```
auth_type = AuthType.UNAUTHORIZED     ← 認証情報を渡していないことの実証

[米津玄師 Lemon]      (1.11秒)  Lemon                     / Kenshi Yonezu / LgSLygQdHS4
[YOASOBI 夜に駆ける]  (0.38秒)  夜に駆ける                 / YOASOBI       / by4SYYWlhEs
[アイドル]            (0.33秒)  アイドル                   / YOASOBI       / m9SMT5ipbxk
[Bohemian Rhapsody]   (0.45秒)  Bohemian Rhapsody (Live Aid) / Queen      / kM0Fpbz0W8U
```

日本語の曲名でも精度は良好で、「アイドル」のように**曲名だけでもアーティストまで正しく解決**できた。レイテンシは初回1.11秒(ウォームアップ込み)、以降0.33〜0.45秒で、会話の体感を損ねない。

**5. 🐛 検索は「見つからない」を返さない(今日の設計上の最重要発見)**

存在しない曲名を投げても0件ではなく、**無関係な曲が1件返ってきた**。

```
[存在しないであろう架空の曲名ZZZQQQ12345]
  → 「時間がなくなってしまいましたが、聞いていただきありがとうございました」(全く無関係)
```

つまり`search()`の結果をそのまま信じると、**音声認識が曲名を聞き間違えた場合に、黙って全然違う曲が再生される**。これは14日目③のルーティング誤爆と同じ「経路は通るが結果が間違っている」型の事故であり、**一致度によるガードが必須**と判断した。

標準ライブラリ`difflib`だけで閾値が引けるかを検証した結果、きれいに分離できた。

| 問い合わせ | ヒットしたタイトル | スコア | 判定 |
|---|---|---|---|
| 米津玄師 Lemon | Lemon | **1.00** | 再生 |
| YOASOBI 夜に駆ける | 夜に駆ける | **1.00** | 再生 |
| アイドル | アイドル | **1.00** | 再生 |
| Bohemian Rhapsody | Bohemian Rhapsody (Live Aid) | **1.00** | 再生 |
| マリーゴールド | マリーゴールド - Marigold | **1.00** | 再生 |
| 存在しないであろう架空の曲名ZZZQQQ12345 | 時間がなくなってしまいましたが…… | **0.14** | 拒否 |
| あさっての天気を教えて | 明日天気になれ - Ashita Tenkininare | **0.11** | 拒否 |

正解が軒並み1.00、無関係が0.11〜0.14と**大きく開いた**ので、**閾値0.5**で安全に切れる。特に「あさっての天気を教えて」(ルーターが誤ってMEDIAへ振ってしまった場合を想定した通常の質問)が0.11で確実に弾かれる点が重要で、**ルーティングが誤爆しても曲は流れない**という二重の防御になる。

**6. Braveの所在**

```bash
reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\brave.exe" //ve
# → C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe
```

`App Paths`に登録されているため、パスをハードコードせず**レジストリから自動検出**できる。

### 分析

14日目⓪で得た「**着手前に実態を確認する**」手順が、今回はセキュリティ監査という別の形で効いた。特に価値があったのは以下の2点である。

1. **「認証なしで検索できる」という事実を、README等の記述ではなくソースコードで確認できたこと。** 最初に読んだREADMEには`YTMusic('oauth.json')`の例しか載っておらず、認証必須のように読める。実際には`auth=None`がデフォルトで、認証パスは`if auth is not None:`で完全に隔離されていた。**ドキュメントの記述だけで安全性を判断していたら、不要にGoogle認証情報を渡す設計にしていた可能性が高い。**
2. **スパイクを実際に走らせたことで「検索が常に何かを返す」という仕様を事前に発見できたこと。** これを知らずに実装していたら、「聞き間違えると無関係な曲が流れる」という原因の分かりにくい不具合を抱え込んでいた。⓪-3(14日目)の`lastAttachedDocumentText`と同様、**動かしてみないと分からない種類の欠陥**である。

### 改善策

- 監査結果を踏まえ、**採用の条件を3つに固定する**。この3条件はコードとテストで強制し、後から緩まないようにする。
  1. **認証なしで使う**(`YTMusic()`は必ず引数なしで生成する。`auth`を渡すコードを書かない)
  2. **検索にのみ使う**(ダウンロード機能は使わない・実装しない)
  3. **`ytmusicapi==1.12.2`にバージョンをピン留めする**(上げるときは⓪と同じ監査を再実行する)
- 14日目④の`google_workspace`と同じく、**`code_executor`のライブラリ・ホワイトリストへ`ytmusicapi`と`media_player`を入れない**。LLMに任意のコードでネットワークライブラリやブラウザ起動を触らせない方針を維持する。

### 残課題

- 非公式ライブラリのため、YouTube側の仕様変更でいつか壊れる。**壊れたときに黙って失敗せず「曲を検索できませんでした」と明示的に返す**設計にして、原因究明のコストを下げる(①で対応)。
- PyPIのサプライチェーン攻撃(メンテナのアカウント乗っ取り)は将来のバージョンで起こりうる。バージョンピン留めと、更新時の再監査を運用として残す。

---

## ① `media_player.py`(新規):曲名の解決と再生

### 背景/目的

⓪の結論に沿って、**「曲名を受け取り、Braveで再生する」ことだけを行う小さなモジュール**を新規に作る。14日目④の`google_workspace.py`と同じ設計思想(**LLMには触らせず、Python側の決まった関数だけが外部を操作する**)を踏襲する。

**方針(相談の上で確定):**

| 論点 | 決定 | 理由 |
|---|---|---|
| 曲名→動画IDの解決 | **`ytmusicapi`(認証なし・検索のみ・v1.12.2ピン留め)** | 無料・回数制限なし。⓪の監査で安全性を確認済み。音楽特化の検索なのでライブ映像や切り抜きを引きにくい |
| 検索結果の信頼性 | **一致度スコア0.5未満は再生せず「見つかりませんでした」と返す** | ⓪-5のとおり検索は常に何かを返すため、ガードが無いと聞き間違いで無関係な曲が流れる。標準ライブラリ`difflib`のみで実装でき、追加依存が要らない |
| 再生方法 | **`subprocess.Popen([brave, "https://www.youtube.com/watch?v=<ID>"])`** | 視聴URLを直接開くのでYouTube側が自動再生する。画面操作(`pyautogui`等)に一切依存しないため壊れにくい |
| Braveのパス解決 | **レジストリ(`App Paths`)→既定インストール先→環境変数`BRAVE_PATH`の順で探索** | ⓪-6のとおりレジストリに登録済み。ハードコードしないことで環境差やアップデートに強くする |
| Braveが無い場合 | **例外を投げず「Braveが見つかりません」と明示的に返す** | 14日目④の`NotAuthenticatedError`と同じ方針。黙って失敗させない |
| LLMからの利用 | **禁止**(`code_executor`のホワイトリストに入れない) | ⓪の改善策のとおり。ブラウザ起動をLLMの生成コードに委ねない |

### 作業内容

- [ ] `requirements.txt`(または環境)へ`ytmusicapi==1.12.2`を**バージョン固定で**追加する
- [ ] `tests/test_media_player.py`(新規)を書く。**ytmusicapiとsubprocessは全てモック**し、ネットワークへ出ず実際にブラウザも起動しないことを保証する
- [ ] `media_player.py`(新規)に`normalize()` / `match_score()`(一致度判定)を実装する
- [ ] `media_player.py`に`search_song(query)`を実装する(`YTMusic()`は**必ず引数なし**で生成する)
- [ ] 一致度が閾値未満のときは`SongNotFoundError`を送出することをテストで固定する(**⓪-5への対応。ここが本命**)
- [ ] `media_player.py`に`resolve_brave_path()`を実装する(レジストリ→既定パス→`BRAVE_PATH`の順。見つからなければ`BraveNotFoundError`)
- [ ] `media_player.py`に`play_song(query)`を実装する(検索→URL組み立て→`subprocess.Popen`。戻り値は曲名・アーティスト・URL)
- [ ] `extract_song_name(utterance)`を実装する(「米津玄師のLemonを流して」→「米津玄師 Lemon」の切り出し)
- [ ] `ytmusicapi`が未インストールの場合でもimportエラーで`voice_gateway.py`全体が落ちないようにする(遅延import+明示メッセージ)

### 実施手順(TDD)

**Step 1: RED — `tests/test_media_player.py`(新規)**

```python
"""15日目①: 音楽再生。
このテストは**一度もネットワークへ出ず、ブラウザも起動しない**。
ytmusicapi.YTMusic と subprocess.Popen を両方モックし、
「正しい引数で呼んだか」「一致度ガードが効くか」だけを見る。
"""
import unittest
from unittest import mock

import media_player


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
        fake_cls.assert_called_once_with()      # ★引数なしであることを固定する

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
        ...


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
```

```powershell
cd C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts
python -m pytest tests/test_media_player.py -v   # 期待: ModuleNotFoundError: No module named 'media_player'
```

**Step 2: GREEN — `media_player.py`(新規)**

```python
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
    """「米津玄師のLemonを流して」→「米津玄師 Lemon」。"""
    ...


def resolve_brave_path() -> str:
    """Braveの実行ファイルを探す。環境変数 → レジストリ → 既定インストール先の順。

    15日目ⓠ-6の実測ではHKLMの App Paths に登録されていたが、環境差や
    アップデートで変わりうるためハードコードしない。
    """
    ...


def search_song(query: str) -> Song:
    """曲名から動画IDを解決する。一致度が閾値未満ならSongNotFoundError。"""
    ytmusic = _ytmusic_class()()          # ★引数なし = 認証なし(採用条件1)
    results = ytmusic.search(query, filter="songs", limit=1)
    ...


def play_song(query: str) -> Song:
    """曲を検索し、BraveでYouTubeの視聴ページを開いて再生する。"""
    brave = resolve_brave_path()
    song = search_song(query)
    subprocess.Popen([brave, song.url])
    return song
```

**Step 3: テストとコミット**

```powershell
python -m pytest tests/test_media_player.py -v
python -m pytest tests/ -q
git add scripts/media_player.py scripts/tests/test_media_player.py scripts/requirements.txt
git commit -m "feat(media): resolve a song name to a YouTube video and play it in Brave"
```

### 結果 / 分析 / 改善策

(実施後に追記)

### 残課題

(実施後に追記)

---

## ② MEDIAルートの新設(`router_rules.py`/`support_ai_auto_pipe.py`修正)

### 背景/目的

14日目③で得た最大の教訓は「**ルーターが振り分けなければ、どれだけ完璧に実装しても一度も実行されない**」だった(`CODE_TRIGGERS`に資料生成の語が1つも無く、`code_executor`が完成しているのに到達しなかった)。同じ失敗を繰り返さないため、**①の実装と並行して、いや①より先にルーティングを通す**。

現在のルートは`FAST` / `DEEP` / `CODE` / `CLARIFY`の4種。ここへ**`MEDIA`**を追加する。

**方針:**

| 論点 | 決定 | 理由 |
|---|---|---|
| 新ルートを作るか、CODEに相乗りするか | **`MEDIA`を新設する** | CODEはLLMにコードを書かせるルートであり、今回は**LLMを一切通さず**`media_player.play_song()`を直接呼ぶ。性質が違うので混ぜない。またLLMを通さないぶん応答が速い(検索0.4秒+起動のみ) |
| トリガーの作り方 | **「曲名+依頼動詞」の形でのみ一致させる** | 14日目③の「資料」と同じ誤爆リスク。「流して」単独では日常会話(「聞き流して」「そのまま流して」)に当たるため、必ず動詞とセットで判定する |
| 誤爆したときの安全性 | **①の一致度ガードが二重の防御になる** | ⓪-5のとおり「あさっての天気を教えて」は一致度0.11で弾かれる。**万一MEDIAへ誤爆しても曲は流れず「見つかりませんでした」と返る** |
| 応答の返し方 | **「〇〇(アーティスト)を再生します」と読み上げる** | 音声対話なので、何が再生されるのか耳で確認できることが重要 |

### 作業内容

- [ ] `tests/test_router_rules.py`に「〇〇を流して/かけて/再生して」が`MEDIA`になるテストを追加する(**最初にやる**)
- [ ] **誤爆しないこと**のテストを書く(「聞き流していいよ」「その話は流して」「今日は音楽を聴いていた」がMEDIAにならない)
- [ ] `router_rules.py`に`MEDIA_TRIGGERS`と判定関数を追加する
- [ ] `router.py`の判定順に`MEDIA`を組み込む(`CODE`より前か後かを決める。「エクセルを流して」のような衝突が無いか確認する)
- [ ] `support_ai_auto_pipe.py`に`MEDIA`ルートの処理を追加する(**LLMを通さず**`media_player.play_song()`を直接呼ぶ)
- [ ] `MediaPlayerError`系を捕捉し、`SongNotFoundError`なら「〇〇という曲が見つかりませんでした」、`BraveNotFoundError`なら「Braveが見つかりません」と**ユーザーに見える形で**返す
- [ ] `tests/test_support_ai_auto_pipe.py`にMEDIAルートの結線テストを追加する(`media_player`をフェイクへ差し替え)

### 実施手順(TDD)

**Step 1: RED — ルーティング(ここが本命)**

```python
# tests/test_router_rules.py に追加
class TestMediaTriggers(unittest.TestCase):
    """15日目②: 音楽再生の依頼がMEDIAルートへ飛ぶこと。

    14日目③の教訓: ここが通らないと①の実装は一度も実行されない。
    """

    def test_play_request_is_media(self):
        self.assertTrue(router_rules.is_media_request("米津玄師のLemonを流して"))

    def test_kakete_is_media(self):
        self.assertTrue(router_rules.is_media_request("アイドルをかけて"))

    def test_saisei_is_media(self):
        self.assertTrue(router_rules.is_media_request("夜に駆けるを再生して"))

    # 誤爆しないこと(「流して」は日常会話に出るので、ここが特に重要)
    def test_kikinagashi_is_not_media(self):
        self.assertFalse(router_rules.is_media_request("その話は聞き流していいよ"))

    def test_casual_music_mention_is_not_media(self):
        self.assertFalse(router_rules.is_media_request("今日は一日音楽を聴いていたよ"))

    def test_water_flow_is_not_media(self):
        self.assertFalse(router_rules.is_media_request("お風呂のお湯を流しておいて"))
```

```python
# router_rules.py への追加案
# 15日目②: 音楽再生の依頼。14日目③の「資料」と同じく、日常会話への誤爆を
# 避けるため名詞単独では一致させない。「流して」は特に日常会話へ出やすい
# (「聞き流して」「お湯を流して」)ため、直前に否定的な文脈が無いことを見る。
MEDIA_TRIGGERS: list[str] = [
    r"(?<!聞き)(?<!き)流して",
    r"(かけて|再生して|聴かせて|聞かせて)",
]
```

> [!warning] 「流して」の誤爆リスクは「資料」より高い
> 14日目③では「資料」「エクセル」が日常会話に出ることを警戒して依頼動詞とのセット判定にした。今回の「流して」は**それ自体が動詞**で、「聞き流して」「お湯を流して」「その話は流して」など**否定的・無関係な用法が非常に多い**。単純な部分一致では確実に誤爆する。
> 幸い①の一致度ガード(ⓠ-5)が最後の砦になるため「誤爆しても曲は流れない」が、**誤爆するたびに「見つかりませんでした」と返ってくるのは体験として悪い**。テストの誤爆ケースを厚めに書いて設計を見張る。

**Step 2: GREEN — `support_ai_auto_pipe.py`へMEDIAルートを結線**

```python
# 15日目②: MEDIAルート。LLMを一切通さず media_player を直接呼ぶ。
# 14日目④のgoogle_workspaceと同じで、外部を操作するのはPython側の
# 決まった関数だけ(LLMが生成したコードには触らせない)。
if route == "MEDIA":
    song_name = media_player.extract_song_name(user_text)
    try:
        song = media_player.play_song(song_name)
    except media_player.SongNotFoundError:
        yield f"「{song_name}」という曲が見つかりませんでした。"
        return
    except media_player.BraveNotFoundError as e:
        yield str(e)
        return
    yield f"{song.title}({song.artist})を再生します。"
    return
```

**Step 3: テストとコミット**

```powershell
python -m pytest tests/test_router_rules.py -k Media -v
python -m pytest tests/ -q
git add scripts/router_rules.py scripts/router.py scripts/openwebui_pipe/support_ai_auto_pipe.py scripts/tests/
git commit -m "feat(router): add a MEDIA route that plays songs without going through the LLM"
```

### 結果 / 分析 / 改善策

(実施後に追記)

### 残課題

(実施後に追記)

---

## ③ UIへの再生チップ表示(`voice_gateway.py`/`index.html`修正)

### 背景/目的

音声で「再生します」と言われても、**どの曲が選ばれたのかは耳だけでは確認しづらい**(特にアーティスト違いや別バージョンを引いた場合)。14日目③④で「生成物チップ」「Google出力のURLチップ」を作ったのと同じ考え方で、**再生した曲を画面にも残す**。

**方針:** 14日目④のURLチップの仕組みをそのまま流用し、応答の下に「🎵 <曲名> / <アーティスト>(YouTubeで開く)」のリンクチップを出す。実装量はごく小さい。

### 作業内容

- [ ] `support_ai_auto_pipe.py`のMEDIAルートの戻り値に曲名・アーティスト・URLを載せる
- [ ] `voice_gateway.py`でWSへ`media_played`イベント(`{title, artist, url}`)を送る
- [ ] `index.html`:`media_played`を受けて応答の下にリンクチップを表示する(14日目④の`appendLinkChip()`を再利用する)
- [ ] `tests/test_voice_gateway.py`に`media_played`イベントの配線テストを追加する

### 結果 / 分析 / 改善策

(実施後に追記)

### 残課題

(実施後に追記)

---

## ④ 成果物一覧(予定)

| ファイル | 新規/修正 | 役割 | 実行方法 | 出力先 |
|---|---|---|---|---|
| `scripts/media_player.py` | **新規** | ①曲名→動画IDの解決(ytmusicapi・認証なし・検索のみ)、一致度ガード、Braveのパス解決、`subprocess.Popen`での再生 | 単体実行はしない(`support_ai_auto_pipe.py`からimportして使う部品) | なし(Braveのタブが開くだけ。ファイルは一切作らない) |
| `scripts/tests/test_media_player.py` | **新規** | ①のテスト。**ytmusicapiとsubprocessを全モック**し、ネットワークへ出ず実際にブラウザも起動しない。**一致度ガードが効くこと**と**`YTMusic()`が引数なしで呼ばれること**を固定する | `python -m pytest tests/test_media_player.py -v` | 標準出力のみ |
| [[サポートAI作製計画/scripts/router_rules.py]] | 修正 | ②`MEDIA_TRIGGERS`の追加。「流して」は日常会話への誤爆が多いため、否定的文脈を除外した設計にする | 単体実行はしない(`router.py`から呼ばれる部品) | — |
| `scripts/tests/test_router_rules.py` | 修正 | ②MEDIAルーティングのテスト。**誤爆しないこと**(「聞き流して」「お湯を流して」)を厚めに含む | `python -m pytest tests/test_router_rules.py -k Media -v` | 標準出力のみ |
| [[サポートAI作製計画/scripts/openwebui_pipe/support_ai_auto_pipe.py]] | 修正 | ②MEDIAルートの処理。**LLMを通さず**`media_player.play_song()`を直接呼び、例外を日本語メッセージへ変換する | OpenWebUIのPipeとして常駐 | — |
| [[サポートAI作製計画/scripts/voice_gateway.py]] | 修正 | ③`media_played`イベントのWS送出 | `python voice_gateway.py --host 127.0.0.1 --port 5055` | なし(常駐サーバー) |
| [[サポートAI作製計画/scripts/static/index.html]] | 修正 | ③再生した曲のリンクチップ表示(14日目④の`appendLinkChip()`を再利用) | `voice_gateway.py`が配信。`http://127.0.0.1:5055/` | ブラウザ表示のみ |
| `scripts/requirements.txt` | 修正 | `ytmusicapi==1.12.2`を**バージョン固定で**追加(⓪の採用条件3) | `pip install -r requirements.txt` | — |

---

## ⑤ 実機確認(チェックリスト)

### 背景/目的

14日目⑥と同じく、**「AIが『再生します』と言ったか」ではなく「実際にBraveで正しい曲が鳴っているか」で判定する**。②のルーティングが通らなければ①は一度も実行されないため、**必ず②から確認する**。

### 事前準備

```powershell
cd C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts
pip install -r requirements.txt          # ytmusicapi==1.12.2 が入る
python voice_gateway.py --host 127.0.0.1 --port 5055
```

### チェックリスト

**②(ルーティング)の確認 — ここから必ず先に**

- [ ] 「米津玄師のLemonを流して」で`[route: MEDIA]`になる(**FASTに落ちたら以降は全部無意味**)
- [ ] 「その話は聞き流していいよ」が**MEDIAに誤爆しない**(FASTのまま)
- [ ] 「今日は一日音楽を聴いていたよ」が**MEDIAに誤爆しない**
- [ ] 「お風呂のお湯を流しておいて」が**MEDIAに誤爆しない**
- [ ] 資料生成(「エクセルにまとめて」)が引き続き`CODE`のまま(MEDIA追加でCODEを奪っていない)

**①(再生)の確認**

- [ ] 「米津玄師のLemonを流して」でBraveが起動し、**Lemonが実際に再生される**
- [ ] Braveが既に起動している場合、**新しいタブで開いて再生される**(二重起動しない)
- [ ] 曲名だけ(「アイドルを流して」)でも正しい曲が再生される
- [ ] 英語の曲名(「Bohemian Rhapsodyを流して」)でも再生される
- [ ] **存在しない曲名を言うと「見つかりませんでした」と返り、無関係な曲が流れない**(ⓠ-5への対応。**本命確認**)
- [ ] 環境変数`BRAVE_PATH`を不正な値にすると「Braveが見つかりません」と**明示的に**返る(スタックトレースではなく)
- [ ] 音声(マイク)からの発話でも再生できる(**本来のユースケース**)
- [ ] 再生中にAIの読み上げと音楽が二重に鳴らないか確認する(**要検討。下記の残課題参照**)

**③(UI)の確認**

- [ ] 応答の下に「🎵 <曲名> / <アーティスト>」のチップが出る
- [ ] チップをクリックするとYouTubeの該当ページが開く

**安全性の再確認(⓪の採用条件が守られているか)**

- [ ] `grep -rn "YTMusic(" scripts/` で**引数ありの呼び出しが1件も無い**(認証情報を渡していない)
- [ ] `pip show ytmusicapi` のバージョンが**1.12.2**である(ピン留めが効いている)
- [ ] `CODE_ACTION_SYSTEM_PROMPT`のホワイトリストに`ytmusicapi`・`media_player`が**入っていない**
- [ ] ダウンロード系の関数(`download`等)を呼んでいる箇所が**1件も無い**

**全体の退行確認**

- [ ] ウェイクワード・自動送信が引き続き動く
- [ ] 14日目①〜④(ピン留め・履歴・資料生成・Google出力)が引き続き動く
- [ ] `python -m pytest tests/ -q` が全件パスする

### 結果 / 分析 / 改善策

(実施後に追記)

### 残課題

(実施後に追記)

---

## 📌 次のステップ

1. **音楽再生中の読み上げの扱いを決める**(①の実機確認で出てくるはずの論点)。音楽が鳴っている最中にAIが喋ると重なる。マイクのミュート制御(10日目①)と同じ仕組みで、再生中はAIの読み上げ音量を下げる/一時停止するなどの案があるが、**Braveの音声はこちらから制御できない**ため、AI側を抑える方向で考える
2. **停止・曲送りへの拡張**(今日は意図的に見送ったもの)。「音楽止めて」でBraveのタブを閉じる案は、`subprocess.Popen`のハンドルを保持しておけば実装できるが、**Braveが既に起動していた場合は新規プロセスにならない**ため確実に閉じられない。タブ単位の制御にはブラウザ拡張かCDP(Chrome DevTools Protocol)接続が要るので、必要になった時点で設計する
3. **ローカル音楽ファイルの再生**(「ローカル優先+YouTubeフォールバック」)。手元に正規に持っている曲はオフラインで即座に鳴らせるため体験が良い。⓪の選択肢で16日目以降の候補として保留した
4. **汎用ブラウザ起動**(「Braveで〇〇を開いて」)。URLホワイトリスト等の安全境界の設計とセットで別途検討する
5. **`ytmusicapi`更新時の再監査**。バージョンを上げるときは15日目ⓠと同じ手順(バイナリの有無・危険呼び出し・通信先・書き込み箇所の検分)を再実行する。この運用を忘れないよう、`requirements.txt`のコメントにも残す
6. 14日目からの持ち越し:
   - 13日目⑥の未消化項目(応答音声の自己トリガー確認、自動送信の猶予中キャンセル、speaking中の割り込み防止)
   - **SearXNGの実ネットワーク疎通確認**
   - **発話内のファイル名からの自動照合**(14日目①で見送り)
   - **引用元チップ**、**テレメトリ/Event Logの実データ化**、**モデル切替**、**思考モードトグル**、**Web検索の自動判定**
   - **VRAM逼迫によるモデル入替スラッシング**、**`Pipe.pipe()`/`synthesize()`の同期呼び出し**
   - **③のグラフ埋め込み・PDF出力・既存ファイル編集・`workspace/`の自動クリーンアップ**
   - **④のOAuth同意画面の本番公開**(テストのままだとトークンが7日で失効)、**`drive.readonly`の追加検討**、**Gmail/カレンダー連携**、**Googleドキュメントへの追記**

### 📋 コード内「〇日目」コメントとノート番号の対応(14日目⓪の改善策を継続)

| コード内 | 内容 | 記載先ノート |
|---|---|---|
| 14日目 | `attached_document_text`、Web検索の`support_ai_auto_pipe.py`結線 | 13日目④追記 / 14日目ⓠ |
| 15日目 | ウェイクワード検出の可視化、読み上げ速度バーの機能化 | 14日目ⓠ(実装済みとして追認) |
| 16・17日目 | `run_turn()`のスレッド化+`cancel_turn` | 14日目ⓠ |
| 18日目 | ウェイクワード「C.L.A.I.R.E.」スペルアウト表記の検出 | 13日目🐛追記 |
| 19日目 | 括弧書きの二重読み解消、`force_finalize_pending()` | 13日目🐛追記 |
| 20日目 | 「Hey, C.L.A.I.R.E.」がforce_finalize再入時に生テキストで再送され誤送信される問題の修正 | 14日目⑥追記 |
| 14日目(ノート) | ①ピン留め ②チャット履歴の永続化 ③資料・コード生成 ④Google Workspace連携 | [[サポートAI作製計画/14日目添付ファイルのピン留め指定とチャット履歴の永続化.md|14日目]] |
| **本ノート(15日目)** | ①`media_player.py` ②MEDIAルート ③再生チップ | 本ノート |

### 🔎 ⓪の監査で参照した情報源

- [ytmusicapi — PyPI](https://pypi.org/project/ytmusicapi/)
- [sigma67/ytmusicapi — GitHub](https://github.com/sigma67/ytmusicapi)
- [ytmusicapi Usage(認証なし利用の記述)— Read the Docs](https://ytmusicapi.readthedocs.io/en/stable/usage.html)
- [ytmusicapi — Snyk Advisor(脆弱性・ヘルススコア)](https://security.snyk.io/package/pip/ytmusicapi)
