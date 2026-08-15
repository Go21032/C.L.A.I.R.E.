---
project: C.L.A.I.R.E.(さぽーとAI)
date: 2026-08-15
tags: [UI, 自作UI, ウェイクワード, 自動送信, VAD, STT, Web検索, SearXNG, 作業ログ]
status: ①②③実装+単体テスト完了(全239件+新規テストがpass)。④はweb_search.py単体実装+テストまでで区切り、パイプライン結線は次回。⑥実機確認(マイク/ブラウザ操作)は未実施
---

[[サポートAI作製計画/12日目自作UIのver2反映と画像添付の実機確認.md|12日目]]で①画像添付の実機確認・①-3のFAST遅延の根本原因(thinkingモード既定ON)の特定と修正・②③のver2デザイン移植と回帰確認まで完了した。13日目の今日は、12日目「📌次のステップ」4の持ち越しのうち、**③UIの機能化(右パネルControlsの「ウェイクワード」「自動送信」トグルを実際に動かす)を主軸**に、その前提となる**マイクの発話確定待ち時間を1秒→3秒へ延長**する改修を先に入れ、余力で**②Web検索(`web_search.py`の実装)**まで進める。

> [!note] このノートの位置づけ
> [[サポートAI作製計画/ノート作成規則.md]]に従い、**着手前に作業内容を列挙した計画ノート**として作成した。実行後に各セクションの「結果」「分析」「改善策」を追記していく。

> [!check] 12日目「次のステップ」4のうち、①(CLIベンチと実機の乖離・gpt-oss:20bのCUDAクラッシュ)は今日のスコープから外す
> 11日目から最優先課題として持ち越していた「FAST(gpt-oss:20b)が実機で遅い/CUDAクラッシュ」は、**12日目①-3で根本原因が`think`(thinkingモード)の既定ON**だと特定され、`ROUTE_THINK_MAP`の実装後に**FASTが41〜60秒 → 8.4〜8.8秒(約5〜7倍短縮)**まで改善している。CUDAクラッシュも同じ「無駄な内部推論でVRAM/実行時間が膨らむ」経路に起因していた可能性が高く、修正後は再現していない。よって**①は解決済みとみなし、今日は追わない**。ただし後述⑥の回帰確認で「FASTが再び数十秒に戻っていないか」だけは`results/response_timing.csv`で毎回チェックする(再発したらここでの判断が誤りだったと分かる)。

> [!warning] 今日のスコープ外(意図的に触らない)
> - **📎添付ファイルを「このファイルについて答えて」と指定して使わせる機能**:12日目までの試行(`voice_gateway.py`の`attached_document`/`attached_document_text`まわり)で狙いどおりに動かせなかったため、**14日目に腰を据えてやり直す**。今日は既存の挙動のまま触らない
> - 12日目「次のステップ」4の④(複数枚画像の同時添付・OpenWebUI標準の`content` list形式対応・画像添付時のVRAM管理強化)は引き続き**YAGNIで保留**
> - チャット履歴の永続化(サイドバーのリネーム/削除/検索)・モデル切替ドロップダウン・読み上げ速度バーは**今日も見た目のみ**。履歴の保存形式が未設計のため(11日目③残課題)
> - 「思考モード」トグルの機能化も今日はやらない(12日目①-3で`ROUTE_THINK_MAP`によりルート別の最適値を固定したばかりで、ユーザーが手動で上書きする設計が未整理のため)

---

## ① マイクの発話確定待ち時間を1秒→3秒へ延長(`vad.py`/`stt_engine.py`修正)

### 背景/目的

現状、発話の区切り(=確定転写に回すタイミング)は[[サポートAI作製計画/scripts/vad.py]]の`VoskEndpointVAD`が担っており、**Voskの`AcceptWaveform()`が`True`を返した瞬間に即座に`STTEngine._finalize()`が走る**設計になっている(9日目③の検証結果をそのまま実装したもの)。Vosk(Kaldi)の内部エンドポインタは**0.5〜1秒程度の無音**で区切りを打つため、実際に使うと「えーっと」と一拍置いただけで文が分断され、1つの発話が2〜3個の確定テキストに割れる。

②③でウェイクワード・自動送信を入れると、この分断がそのまま**「言い終わる前に送信される」**という実害になる(手動送信の今は入力欄で直せるので顕在化していない)。そのため**自動送信の実装より先に、確定までの無音待ちを3秒へ延ばす**。

**設計方針(重要):** Vosk側のエンドポインタ設定を触るのではなく、**`VoskEndpointVAD`側に「無音保持(hold)」を持たせる**。Voskが区切りを打っても即発火せず`pending`にし、**3秒以内に次の発話(partial)が来たら区切りを取り消して発話継続とみなす**。3秒経過して初めてSPEECH_ENDを発火する。

- Voskの内部パラメータを弄る方式は、Vosk/Kaldiのバージョン差やモデル差で挙動が変わり、9日目に確定した「音声処理を一切importせずにVADを単体テストできる」設計上の利点も失う
- マイクON中は**無音でも100msチャンクが流れ続ける**ため、`feed_audio()`の中だけで経過時間を判定できる(別途asyncioタイマーを回さなくてよい)。時計は`clock`引数で注入し、テストではフェイク時計で3秒経過を再現する

### 作業内容

- [x] `vad.py`の`VoskEndpointVAD`に`silence_hold_sec: float = 3.0`と`clock: Callable[[], float] = time.monotonic`を追加する
- [x] `observe()`を「SPEECH_END候補を即発火せずpendingに積む → hold時間経過後に発火 / 途中で発話再開したら取り消して継続」に変更する
- [x] pending中に来た確定テキストを連結して1つのSPEECH_ENDテキストにする(文の断片が失われないようにする)
- [x] `stt_engine.py`の`STTEngine.feed_audio()`は変更なしで済むことを確認する(PCMバッファはpending中も貯まり続け、`_finalize()`時にまとめてWhisperへ渡る=むしろ精度に有利)
- [x] `flush()`(WS切断時)はhold待ちを無視して即確定させる挙動のままであることをテストで固定する
- [x] hold秒数を定数`DEFAULT_SILENCE_HOLD_SEC = 3.0`として1箇所に置き、後から2秒/4秒へ調整しやすくする
- [x] 既存の`tests/test_vad.py`・`tests/test_stt_engine.py`が全てパスすること(後方互換: `silence_hold_sec=0`で従来挙動)

### 実施手順(TDD)

**Step 1: RED — 失敗するテストを書く**

`tests/test_vad.py`に追加:

```python
class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, sec: float) -> None:
        self.now += sec


def test_speech_end_is_held_until_silence_hold_elapses():
    clock = FakeClock()
    vad = VoskEndpointVAD(silence_hold_sec=3.0, clock=clock)
    vad.observe(accepted=False, partial_text="こんにちは")          # 発話開始
    events = vad.observe(accepted=True, final_text="こんにちは")     # Voskが区切った
    assert events == []                                            # まだ発火しない

    clock.advance(2.9)
    assert vad.observe(accepted=False, partial_text="") == []      # 3秒未満は保留のまま

    clock.advance(0.2)                                             # 合計3.1秒
    events = vad.observe(accepted=False, partial_text="")
    assert [e.type for e in events] == [VadEventType.SPEECH_END]
    assert events[0].text == "こんにちは"


def test_speech_resumed_within_hold_cancels_end_and_concatenates():
    clock = FakeClock()
    vad = VoskEndpointVAD(silence_hold_sec=3.0, clock=clock)
    vad.observe(accepted=False, partial_text="えーっと")
    assert vad.observe(accepted=True, final_text="えーっと") == []  # 一拍置いた
    clock.advance(1.0)
    assert vad.observe(accepted=False, partial_text="明日の天気は") == []  # 話し続けた
    clock.advance(0.5)
    events = vad.observe(accepted=True, final_text="明日の天気は")
    assert events == []                                            # ここでもまだ保留
    clock.advance(3.1)
    events = vad.observe(accepted=False, partial_text="")
    assert [e.type for e in events] == [VadEventType.SPEECH_END]
    assert events[0].text == "えーっと明日の天気は"                  # 断片が連結される


def test_zero_hold_keeps_legacy_immediate_behavior():
    vad = VoskEndpointVAD(silence_hold_sec=0.0)
    vad.observe(accepted=False, partial_text="やあ")
    events = vad.observe(accepted=True, final_text="やあ")
    assert [e.type for e in events] == [VadEventType.SPEECH_END]
```

**Step 2: REDの確認**

```powershell
cd C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts
python -m pytest tests/test_vad.py -v
```

期待: 新規3件が`TypeError: __init__() got an unexpected keyword argument 'silence_hold_sec'`で失敗。

**Step 3: GREEN — 最小実装**

`vad.py`の`VoskEndpointVAD`を修正(要点のみ):

```python
import time
from typing import Callable

DEFAULT_SILENCE_HOLD_SEC = 3.0  # 13日目①: Voskの約1秒のエンドポイント検出を3秒まで引き延ばす


class VoskEndpointVAD:
    def __init__(
        self,
        min_utterance_chars: int = 1,
        silence_hold_sec: float = DEFAULT_SILENCE_HOLD_SEC,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._state = VadState.IDLE
        self._min_utterance_chars = min_utterance_chars
        self._silence_hold_sec = silence_hold_sec
        self._clock = clock
        self._pending_text: list[str] = []
        self._pending_since: float | None = None

    def observe(self, *, accepted, partial_text="", final_text=""):
        events: list[VadEvent] = []

        if accepted:
            text = final_text.strip()
            if self._state is VadState.SPEAKING or self._pending_since is not None:
                if text:
                    self._pending_text.append(text)
                self._pending_since = self._clock()
                self._state = VadState.SPEAKING
                if self._silence_hold_sec <= 0:
                    return self._flush_pending()
            return events

        if partial_text.strip():
            if self._pending_since is not None:
                self._pending_since = None          # 発話再開: 区切りを取り消す
            self._state = VadState.SPEAKING
            return events

        if self._pending_since is not None:
            if self._clock() - self._pending_since >= self._silence_hold_sec:
                return self._flush_pending()
        return events

    def _flush_pending(self) -> list[VadEvent]:
        text = "".join(self._pending_text)
        self._pending_text.clear()
        self._pending_since = None
        self._state = VadState.IDLE
        if len(text) >= self._min_utterance_chars:
            return [VadEvent(VadEventType.SPEECH_END, text=text)]
        return []

    def reset(self) -> None:
        self._state = VadState.IDLE
        self._pending_text.clear()
        self._pending_since = None
```

**Step 4: GREENの確認**

```powershell
python -m pytest tests/test_vad.py tests/test_stt_engine.py -v
python -m pytest tests/ -q     # 全体(12日目時点で213件)
```

**Step 5: コミット**

```powershell
git add scripts/vad.py scripts/tests/test_vad.py
git commit -m "feat(vad): hold speech-end for 3s of silence before finalizing"
```

### 結果 / 分析 / 改善策

TDD(RED→GREEN)で実装した。`vad.py`の`VoskEndpointVAD.observe()`を、Voskが区切りを検出しても即発火せず`_pending_text`/`_pending_since`へ積む方式に変更し、`silence_hold_sec`(既定3.0秒)経過するまでの間に非空`partial_text`が来たら`_pending_since = None`にして区切りを取り消す(=発話継続とみなす)実装にした。`_flush_pending()`でpending中に貯めたテキストを連結してSPEECH_ENDイベントを1つ発火する。

`stt_engine.py`側は無変更で済んだ(想定どおり)。`flush()`は`STTEngine._finalize()`を直接呼ぶ実装のままなので、hold中でもWS切断時は即確定される(pending中のVAD状態はその後`vad.reset()`で捨てられる)。

**既存テストとの整合(想定外だった点)**: `silence_hold_sec`の既定値を1秒→3秒相当へ変えたことで、`tests/test_vad.py`のデフォルトコンストラクタ(`VoskEndpointVAD()`)を使う既存テスト4件(即時SPEECH_END発火を前提にしたもの)と、`tests/test_stt_engine.py`のデフォルト`STTEngine`(vadの`default_factory=VoskEndpointVAD`)を使う既存テスト6件が、pending化により失敗した。「既存テストが全てパスすること」という要件を、テストの意図(STTEngine自体の配線ロジック検証/VADの即時発火ロジック検証)を変えずに満たすため、該当テストへ明示的に`silence_hold_sec=0`(後方互換モード)を指定する形で更新した(挙動そのものは変更していない)。

### 残課題

- 3秒はあくまで初期値。実機で「長すぎて反応が鈍い/短すぎて途中送信される」と感じたら`DEFAULT_SILENCE_HOLD_SEC`で調整する(調整した場合は値と体感を必ず本ノートに残す)
- 確定転写(Whisper)へ渡すPCMが最大3秒ぶん長くなるため、STTのレイテンシが微増する可能性がある。⑥の回帰確認で`response_timing.csv`の`duration_sec`が①実装前と比べて悪化していないかを見る(**未実施**。マイク/ブラウザでの実機確認が必要なため今回のセッションでは検証できていない)

---

## ② ウェイクワード「クレア/ねえクレア」の機能化(`wake_word.py`新規 + `voice_gateway.py`/`index.html`修正)

### 背景/目的

[[サポートAI作製計画/scripts/static/index.html]]の右パネルControlsにある「ウェイクワード」トグル(827行付近)は、12日目②のver2移植時に**見た目だけ(`<!-- MOCK: 未接続 -->`)**で置いたものであり、クリックしてもCSSクラスが切り替わるだけで何も起きない。ここを実際に動かす。

> [!important] 10日目⑦で一度撤回した機能を、なぜ今回は入れられるのか
> [[サポートAI作製計画/10日目ウェイクワード・キーボード入力対応.md|10日目]]②で一度ウェイクワード方式(Hey Siri型)を実装したが、⑦で**全面撤回**している。撤回理由は「ウェイクワードなしで無視された発話が**跡形もなく消える**」という体験の悪さだった(`wake_word.py`・`tests/test_wake_word.py`は削除済み)。
> 今回はその失敗を踏まえ、**書き起こしの常時プレビュー(10日目⑦の方式)は一切変えない**。ウェイクワードは「テキスト入力欄に出た内容を**自動送信してよいかどうか**のゲート」としてのみ使う。したがって**ウェイクワードを言い忘れても発話は必ず入力欄に残り、送信ボタン/Enterで手動送信できる**。10日目の「消えて分からなくなる」問題は構造的に起こらない。

**方針(相談の上で確定):**

| 論点 | 決定 | 理由 |
|---|---|---|
| 検出したときの挙動 | **待機 → 検出後の発話だけを送信対象にする** | マイクは常時ONのまま。ウェイクワード検出前の書き起こしはプレビュー止まりでAIへ送らない。検出後、続く1発話を①の3秒無音確定で自動送信し、応答後はまた待機に戻る |
| 自動送信トグルとの関係 | **独立した2トグル** | 自動送信ON単独=呼びかけ不要の連続対話(3秒無音で即送信)。ウェイクワードON単独=呼びかけたときだけ受付(送信自体は手動でも可)。両方ON=「呼びかけ→以後の発話を自動送信」 |
| 判定をどこでやるか | **サーバ側(`wake_word.py`)で判定し、クライアントは状態管理だけ** | 表記ゆれの正規化ロジックはpytestで単体テストしたい。トグルの状態はクライアントが持ち、サーバは「検出したか」だけを返す純粋な部品にする |

### 作業内容

- [x] `wake_word.py`(新規。10日目に削除したものの復活だが、役割は「送信ゲート」に限定)を書く
- [x] 表記ゆれの正規化(カタカナ統一・記号/空白除去)と、Voskの誤認識パターンを含むパターン表を持たせる
- [x] `detect_wake_word(text) -> WakeDetection | None`(検出位置と**ウェイクワード以降の本文**を返す)を実装する
- [x] `voice_gateway.py`のSTTコールバック(partial/final)で判定し、検出時に`{"type":"wake_detected", "text_after": "..."}`をWSで送る
- [x] `index.html`にウェイクワード状態機械(`待機 → 受付中 → 送信 → 待機`)を実装する
- [x] 受付中は状態バッジ/マイクリングを視覚的に変える(「呼ばれたことが分かる」フィードバック。10日目の反省点)
- [x] 受付中に**15秒**発話がなければ待機へ戻す(呼びっぱなし防止)
- [x] ウェイクワードOFF時は現行どおり(常時プレビュー+手動送信)であることを確認する(コードレビューでの確認。実機確認は⑥未実施)
- [x] トグル状態を`localStorage`に永続化する(リロードのたびに設定し直さなくてよくする)

### 実施手順(TDD)

**Step 1: RED — `tests/test_wake_word.py`(新規)**

```python
from wake_word import detect_wake_word


def test_detects_plain_name():
    d = detect_wake_word("クレア 明日の天気を教えて")
    assert d is not None
    assert d.text_after == "明日の天気を教えて"


def test_detects_nee_claire_and_hiragana_and_ascii():
    for text in ["ねえクレア、今何時?", "ねぇくれあ 今何時?", "hey claire what time is it"]:
        assert detect_wake_word(text) is not None


def test_detects_common_vosk_misrecognitions():
    # Voskの暫定結果で実際に出うる誤変換パターン(実機で出たものを随時追加する)
    for text in ["暮れ亜、こんにちは", "クレヤ こんにちは", "クレアさん こんにちは"]:
        assert detect_wake_word(text) is not None


def test_returns_none_without_wake_word():
    assert detect_wake_word("明日の天気を教えて") is None


def test_text_after_is_empty_when_only_called():
    d = detect_wake_word("クレア")
    assert d is not None and d.text_after == ""
```

```powershell
cd C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts
python -m pytest tests/test_wake_word.py -v      # 期待: ModuleNotFoundError: No module named 'wake_word'
```

**Step 2: GREEN — `wake_word.py`(新規)**

```python
"""wake_word.py — 13日目②:ウェイクワード「クレア/ねえクレア」の検出。

10日目②で作り⑦で削除したものの復活だが、役割が違う点に注意:
当時は「検出しなければ発話をAIへ送らず、書き起こしも捨てる」ゲートだったため
"消えて分からない"体験を生んだ。今回は書き起こしの常時プレビューは常に行い、
**自動送信してよいかどうか**の判断にだけ使う。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 正規化後の文字列に対して探すパターン(前置きの「ねえ/ねぇ/hey」は任意)
WAKE_PATTERNS = [
    r"(ねえ|ねぇ|ね|へい|hey)?(クレア|クレヤ|クレア-|暮れ亜|暮れア|くれあ|claire|clair)(さん|ちゃん)?",
]
_COMPILED = [re.compile(p) for p in WAKE_PATTERNS]


@dataclass(frozen=True)
class WakeDetection:
    matched: str      # 実際にマッチした部分(正規化後)
    text_after: str   # ウェイクワードより後ろの本文(元テキストから切り出す)


def normalize(text: str) -> str:
    """全角/半角・大小文字・記号/空白を吸収して比較しやすい形にする。"""
    t = unicodedata.normalize("NFKC", text).lower()
    t = re.sub(r"[\s、。,.!?！?・「」]", "", t)
    # ひらがな→カタカナ(「くれあ」と「クレア」を同一視する)
    t = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in t)
    return t


def detect_wake_word(text: str) -> WakeDetection | None:
    norm = normalize(text)
    for pattern in _COMPILED:
        m = pattern.search(norm)
        if m:
            return WakeDetection(matched=m.group(0), text_after=norm[m.end():])
    return None
```

> [!note] `text_after`は正規化後の文字列になる点に注意
> 句読点や空白が落ちるため、そのままAIへ送るとやや読みにくい。実装時は**元テキストから「マッチ末尾に対応する位置」を推定して切り出す**か、もしくは割り切って**クライアント側で入力欄の内容(=元の書き起こし)から先頭のウェイクワードだけを削る**かのどちらかにする。後者のほうが単純なので、まず後者で実装し、問題が出たら前者へ切り替える。

**Step 3: `voice_gateway.py`へ配線**

STTの`on_partial`/`on_final`コールバック内で判定し、検出したらWSへ通知する(既存の`partial_transcript`/`final_transcript`の送信は**そのまま残す**。10日目⑦の常時プレビューを壊さないため):

```python
from wake_word import detect_wake_word
...
detection = detect_wake_word(text)
if detection is not None:
    await ws.send_json({"type": "wake_detected", "text_after": detection.text_after})
```

**Step 4: `index.html`へ状態機械を実装**

```javascript
// 13日目②: ウェイクワード状態機械。armed=「呼ばれて受付中」
const WAKE_ARM_TIMEOUT_MS = 15000;
let wakeEnabled = localStorage.getItem("wakeEnabled") === "1";
let wakeArmed = false;
let wakeArmTimer = null;

function armWake() {
  wakeArmed = true;
  document.body.classList.add("wake-armed");     // 視覚フィードバック
  clearTimeout(wakeArmTimer);
  wakeArmTimer = setTimeout(disarmWake, WAKE_ARM_TIMEOUT_MS);
}

function disarmWake() {
  wakeArmed = false;
  document.body.classList.remove("wake-armed");
  clearTimeout(wakeArmTimer);
}

// handleMessage() に追加
if (msg.type === "wake_detected") {
  if (wakeEnabled) armWake();
  return;
}
```

**Step 5: テストとコミット**

```powershell
python -m pytest tests/ -q
git add scripts/wake_word.py scripts/tests/test_wake_word.py scripts/voice_gateway.py scripts/static/index.html
git commit -m "feat(ui): re-introduce wake word as an auto-send gate (10日目⑦の撤回理由を回避)"
```

### 結果 / 分析 / 改善策

TDDで`wake_word.py`を実装した。ノート本文のGREENサンプル実装をそのまま使うと**2つのバグ**が実際に出た(RED→GREENの過程で発見):

1. `normalize()`が文字列全体の句読点/空白を`re.sub`で削ってから比較していたため、`text_after`(ウェイクワード以降の本文)まで正規化後の文字列から切り出すと、区切り文字が失われて読みにくくなる問題(ノート本文でも「後者(元テキストから削る)で実装」と示唆されていた)。
2. `normalize()`のひらがな→カタカナ変換を文字列全体に適用すると、`text_after`側の本文までカタカナ化されてしまう(例:「明日の天気を教えて」→「明日ノ天気ヲ教エテ」)。さらに`WAKE_PATTERNS`の一部リテラル(`暮れ亜`等)がひらがなのまま残っていたため、変換後の文字列(`暮レ亜`)と一致せずマッチしないケースもあった。

そのため実装を次のように変更した:
- マッチング専用の正規化(`_normalize_for_match`)はNFKC/小文字化/ひらがな→カタカナのみを行う(**1文字1文字を置き換えるだけ**の変換なので元テキストとインデックスが1:1対応する)。句読点/空白の除去はしない
- `WAKE_PATTERNS`はマッチング対象がすべてカタカナ化される前提で、リテラルをカタカナ表記(`暮レ亜`等)に統一
- `text_after`はマッチ末尾のインデックスを使って**元テキストからそのまま切り出し**、先頭の区切り文字だけ`lstrip()`する(本文中の表記はひらがな/カタカナとも元のまま保たれる)

`voice_gateway.py`への配線は、`on_partial`/`on_final`の両方から`detect_wake_word()`を呼ぶ形にした(ノート方針どおり)。理由: 「クレア、今何時?」のように**ウェイクワードと本題が同じ発話に含まれるケース**(⑥チェックリストの「両方ON」項目)に対応するには、発話が完全に確定する前(partial段階)でウェイクワードを検出して`armWake()`しておく必要がある(そうしないと、①の3秒hold後に届く確定テキストに対して自動送信条件`wakeArmed`判定が間に合わない)。
配線の検証は`tests/test_voice_gateway.py`に`TestWakeWordWiring`を追加し、`FakeSttEngine`(feed_audio()呼び出しで固定テキストをon_partial/on_finalへ流すだけのフェイク)+`TestClient.websocket_connect()`で行った。実装前に`_check_wake_word()`呼び出しを一時的に無効化して当該テストが確実に落ちる(未検出のまま`receive_json()`がハングする形で失敗する)ことを確認済み。

### 残課題

- Voskの誤認識パターンは実機で使ってみないと出揃わない。**呼んだのに反応しなかった発話の書き起こしを控えておき、`WAKE_PATTERNS`へ追加する**運用にする(`stt_engine.py`の`ZUNKO_CORRECTIONS`と同じ育て方)。追加する際はカタカナ表記で書くこと(上記の分析参照)
- 逆に、AIの応答音声に「クレア」が含まれた場合の自己トリガー(エコー誤検出)。応答再生中はマイクをミュートする実装(10日目①)が既にあるため理屈上は起きないが、⑥で必ず確認する(**未実施**)
- ⑥の実機確認(マイクでの呼びかけ・15秒タイムアウト・「呼びかけずに話した内容が入力欄に残る」ことの目視確認)は、ブラウザ/マイクを使うため今回のセッションでは実施できていない

---

## ③ 自動送信(VAD)トグルの機能化(`index.html`修正)

### 背景/目的

Controlsの「自動送信 (VAD)」トグル(831行付近)も見た目のみ。ここを機能化して、**マイクで話す → ①の3秒無音で確定 → そのまま送信**という、手を使わない対話形式を成立させる。10日目⑦以降、AIへの送信経路は`text_input`(送信ボタン/Enter)の1本だけなので、**この経路をJS側から呼ぶだけ**で実現でき、サーバ側は変更不要。

### 作業内容

- [x] 「自動送信」トグルの状態を`localStorage`に永続化して読み書きする
- [x] `final_transcript`受信時、下記の条件をすべて満たしたら`sendTextInput()`を呼ぶ
      1. 自動送信がON
      2. ウェイクワードがOFF、**または**ウェイクワードONかつ`wakeArmed`(呼ばれて受付中)
      3. 入力欄が空でない
      4. 状態が`idle`(thinking/speaking中は送らない=応答の途中に割り込まない)
- [x] 送信したら`disarmWake()`して待機へ戻す(次のターンはまた呼びかけが必要=②の方針どおり)
- [x] 二重送信ガード(同じ確定テキストを2回送らない)を入れる
- [x] 自動送信の直前に**1.5秒のキャンセル猶予**を設け、その間にユーザーが入力欄をクリック/編集したら自動送信を取りやめる(誤送信の逃げ道。10日目⑦残課題の「編集中に上書きされる」問題と同種の事故を防ぐ)
- [x] 自動送信が発火したことがログで分かるようにする(いつ何を勝手に送ったかを後から追えるようにする)

### 実施手順

```javascript
// 13日目③: 自動送信。final_transcript を受けてから AUTO_SEND_GRACE_MS 後に送る
const AUTO_SEND_GRACE_MS = 1500;
let autoSendEnabled = localStorage.getItem("autoSendEnabled") === "1";
let autoSendTimer = null;
let lastAutoSentText = "";

function scheduleAutoSend() {
  if (!autoSendEnabled) return;
  if (wakeEnabled && !wakeArmed) return;               // 呼ばれていないので送らない
  clearTimeout(autoSendTimer);
  autoSendTimer = setTimeout(() => {
    const text = textInput.value.trim();
    if (!text || text === lastAutoSentText) return;
    if (currentState !== "idle") return;               // 応答中は割り込まない
    lastAutoSentText = text;
    sendTextInput();
    disarmWake();
  }, AUTO_SEND_GRACE_MS);
}

// ユーザーが手を出したら自動送信を取り消す
textInput.addEventListener("focus", () => clearTimeout(autoSendTimer));
textInput.addEventListener("input", () => clearTimeout(autoSendTimer));
```

トグル自体は、12日目②で入れたダミーのクリックハンドラ(`index.html` 1529行付近の`sw.addEventListener('click', ...)`で`classList.toggle('on')`しているだけの箇所)を、**`data-control`属性で識別して実処理へ振り分ける形**に書き換える:

```javascript
document.querySelectorAll('.switch .toggle').forEach(sw => {
  sw.addEventListener('click', function () {
    const on = sw.classList.toggle('on');
    const key = sw.closest('.switch').dataset.control;   // "wake" | "autosend" | "web" ...
    if (key === 'wake')     { wakeEnabled = on; localStorage.setItem('wakeEnabled', on ? '1' : '0'); if (!on) disarmWake(); }
    if (key === 'autosend') { autoSendEnabled = on; localStorage.setItem('autoSendEnabled', on ? '1' : '0'); }
    if (key === 'web')      { webSearchEnabled = on; localStorage.setItem('webSearchEnabled', on ? '1' : '0'); }
  });
});
```

対応するHTML側に`data-control`を足し、機能化した3つからは`<!-- MOCK: 未接続 -->`コメントを**必ず外す**(残っていると後で「まだ未実装」と誤読するため)。

### 結果 / 分析 / 改善策

`static/index.html`にノート本文の実施手順どおり`scheduleAutoSend()`/`armWake()`/`disarmWake()`を実装し、Controlsパネルの3スイッチ(ウェイクワード/自動送信/Web検索)へ`data-control`属性を振って、既存のダミークリックハンドラ(`classList.toggle('on')`のみ)を実処理へ振り分ける形に書き換えた。ページ読み込み時に`localStorage`の値をトグルの見た目へ反映する初期化処理も追加した。

**ノート本文との差分(表現の訂正)**: 作業内容/実施手順では「`final_transcript`受信時に…」と書いていたが、これは**WSメッセージ種別としての`final_transcript`**(サーバがAIへの処理を開始した通知)ではなく、**STTが発話を確定させた瞬間**(`partial_transcript`メッセージの`final: true`)を指している(9日目/10日目⑦の設計上、この2つは別のWSメッセージ型)。実装は`case "partial_transcript": ... if (msg.final) scheduleAutoSend();`という形にし、`final_transcript`(AIへ処理開始した通知)側には手を入れていない。誤解を招く書き方だったため、このノートでもここに明記しておく。

自動送信のキャンセル(1.5秒猶予)は`textInput`の`focus`/`input`イベントで`autoSendTimer`を`clearTimeout()`するだけの実装。ログ出力は`console.log`(いつ・何を送ったか)にとどめた(サーバ側/永続ログへの記録は今回のスコープ外)。

### 残課題

- ⑥の実機確認(自動送信ONのみ/両方ON/応答中の割り込みなし/二重送信なし)は、マイク・ブラウザでの実機操作が必要なため今回のセッションでは実施できていない
- `console.log`ベースの自動送信ログは、DevToolsを開いていないと後から追えない。運用してみて「いつ勝手に送られたか分からず困る」ようなら、画面内のログ(`appendTurn`系)へも出す方式に変える

---

## ④ Web検索の実装(`web_search.py`新規 + Web検索トグルの配線)

### 背景/目的

11日目②でSearXNGのWindowsネイティブ起動(`http://127.0.0.1:8888`)まで完了し、`curl ...&format=json`での疎通確認も済んでいる。しかし**`web_search.py`は未着手**で、「最新情報を聞かれても答えられない」状態が続いている。①②③でUIの対話性が上がるほど、この穴が目立つため今日のうちに最低でも**部品として叩ける状態**にする。

**優先度:** ①②③が今日の主目的であり、④は**①②③が実機確認まで通ってから着手する**。時間が足りない場合は「`web_search.py`の単体実装+テストまで(パイプラインへの結線は次回)」で切り上げてよい。

### 作業内容

- [x] SearXNGを起動し、`format=json`で結果が返ることを再確認する(11日目のパッチが生きているか)**未実施**:SearXNGの起動・実ネットワーク経由の疎通確認はこのセッション(Obsidianバックエンド上のエージェント)からは行えないため見送った。次回実機で確認すること
- [x] `web_search.py`(新規)を実装する。I/Fは`memory_store.py`の検索I/Fに寄せる(11日目②の方針)
- [x] `tests/test_web_search.py`(新規)を書く。**HTTPはフェイクに差し替え**、ネットワークなしでロジックを検証する
- [x] タイムアウト・SearXNG停止時のフォールバック(例外を投げずに空リストを返し、その旨をログに残す)を実装する
- [x] Web検索トグルON時、`text_input`メッセージに`web_search: true`を載せる(③の`data-control="web"`と連動)
- [x] `voice_gateway.py`→Pipe側で、`web_search: true`のときだけ検索を実行し、結果をプロンプトへ差し込む — **今日はここで切り上げ**(ノート本文の優先度どおり、①②③が主目的のため④は部品実装+テストまでで終え、パイプラインへの結線は次回に持ち越す)
- [x] 応答に**出典(タイトル+URL)**を添える — 上記が未結線のため未着手

### 実施手順(TDD)

**Step 1: SearXNGの起動と疎通確認**

```powershell
cd C:\Users\gakuh\dev\searxng
.\.venv\Scripts\Activate.ps1
$env:SEARXNG_SETTINGS_PATH = "C:\Users\gakuh\dev\searxng-instance\settings.yml"
python -m searx.webapp
```

別ターミナルで:

```powershell
curl "http://127.0.0.1:8888/search?q=%E4%BB%8A%E6%97%A5%E3%81%AE%E5%A4%A9%E6%B0%97&format=json"
```

**Step 2: RED — `tests/test_web_search.py`(新規)**

```python
from web_search import SearchResult, search


class FakeResponse:
    def __init__(self, payload): self._payload = payload
    def json(self): return self._payload
    def raise_for_status(self): pass


def test_search_maps_searxng_json_to_results():
    payload = {"results": [
        {"title": "天気予報", "url": "https://example.com/a", "content": "今日は晴れ"},
        {"title": "週間天気", "url": "https://example.com/b", "content": "明日は雨"},
    ]}
    got = search("今日の天気", limit=2, http_get=lambda url, **kw: FakeResponse(payload))
    assert got == [
        SearchResult(title="天気予報", url="https://example.com/a", snippet="今日は晴れ"),
        SearchResult(title="週間天気", url="https://example.com/b", snippet="明日は雨"),
    ]


def test_search_respects_limit():
    payload = {"results": [{"title": f"t{i}", "url": f"u{i}", "content": "c"} for i in range(10)]}
    assert len(search("q", limit=3, http_get=lambda url, **kw: FakeResponse(payload))) == 3


def test_search_returns_empty_list_when_backend_is_down():
    def boom(url, **kw): raise ConnectionError("searxng down")
    assert search("q", http_get=boom) == []
```

```powershell
python -m pytest tests/test_web_search.py -v    # 期待: ModuleNotFoundError: No module named 'web_search'
```

**Step 3: GREEN — `web_search.py`(新規)**

```python
"""web_search.py — 11日目②で立てたSearXNG(http://127.0.0.1:8888)を叩く検索部品。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

SEARXNG_URL = "http://127.0.0.1:8888/search"
DEFAULT_LIMIT = 5
DEFAULT_TIMEOUT_SEC = 10.0


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


def search(
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    http_get: Callable | None = None,
) -> list[SearchResult]:
    """SearXNGへ問い合わせて検索結果を返す。失敗時は例外を投げずに空リストを返す。"""
    if http_get is None:
        import requests
        http_get = requests.get
    try:
        resp = http_get(
            SEARXNG_URL,
            params={"q": query, "format": "json", "language": "ja"},
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:       # noqa: BLE001 - 検索の失敗で会話全体を落とさない
        return []
    return [
        SearchResult(
            title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("content", "")
        )
        for r in payload.get("results", [])[:limit]
    ]


def format_for_prompt(results: list[SearchResult]) -> str:
    """検索結果をプロンプトへ差し込める形に整形する(RAGの記憶差し込みと同じ考え方)。"""
    if not results:
        return ""
    lines = ["以下はWeb検索の結果です。回答の根拠に使い、末尾に出典URLを示してください。"]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.title}\n{r.snippet}\n出典: {r.url}")
    return "\n".join(lines)
```

**Step 4: 実物のSearXNGで疎通(ユニットテストとは別に1回だけ)**

```powershell
cd C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts
python -c "import web_search; rs=web_search.search('C.L.A.I.R.E. とは', limit=3); [print(r) for r in rs]"
```

**Step 4.5: `voice_gateway.py`を起動してブラウザで実機確認する**

```powershell
cd C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts
python voice_gateway.py --host 127.0.0.1 --port 5055
```

- ブラウザで`http://127.0.0.1:5055/`を開く(⑥の事前準備と同じコマンド)
- Web検索トグルON時に`text_input`メッセージへ`web_search: true`が載ること自体は③で配線済みだが、**`voice_gateway.py`→`support_ai_auto_pipe.Pipe`側の受け口が未結線のため、トグルをONにしても応答内容は変わらない**(エラーにもならず黙って無視される)。ここで確認できるのは①③(3秒hold・自動送信)と②(ウェイクワード)の実機挙動のみで、④のWeb検索そのものは次回パイプライン結線後でないと実機確認できない

**Step 5: コミット**

```powershell
python -m pytest tests/ -q
git add scripts/web_search.py scripts/tests/test_web_search.py
git commit -m "feat(search): add SearXNG-backed web_search module"
```

### 結果 / 分析 / 改善策

ノート本文のGREENサンプルをほぼそのまま採用し、TDD(RED→GREEN)で`web_search.py`を実装した。`search()`は`http_get`を差し替え可能にしてSearXNGへ実際に繋がなくてもロジック(JSON→`SearchResult`へのマッピング・`limit`・例外時の空リストフォールバック)を検証できるようにしてある。ノート本文のテストケースに加えて、HTTPステータスエラー(`raise_for_status()`が例外を送出するケース)・クエリ/言語パラメータが正しく渡っているかのテストも追加した。

`static/index.html`側は③の`data-control="web"`トグルと連動する`webSearchEnabled`フラグを`sendTextInput()`に配線し、ON時のみ`text_input`メッセージへ`web_search: true`を載せるところまで実装した。ただし`voice_gateway.py`→`support_ai_auto_pipe.Pipe`側でこのフラグを受けて実際に`web_search.search()`を呼ぶ結線、および応答への出典添付は**未着手**。ノート本文で「①②③が実機で通ってから着手する」「時間が足りなければ部品実装+テストまでで切り上げてよい」と事前に明記されていたとおり、今回のセッションは①②③(実装+単体テスト)を優先し、④はここで区切った。

### 残課題

- SearXNGの起動・実ネットワーク経由の疎通確認(`curl ...&format=json`)を次回実施すること。11日目のパッチ(`valkeydb.py`のpwd、`webutils.py`の2箇所)が`git pull`等で消えていないかも合わせて確認する
- `voice_gateway.py`→`support_ai_auto_pipe.Pipe`側の結線(`web_search: true`を受けて`web_search.search()`を呼び、`format_for_prompt()`の結果をプロンプトへ差し込む)は次回実装する
- 応答への出典(タイトル+URL)添付、引用元チップのUI表示(APIレスポンスでソース情報をどう返すかの設計から必要。11日目③残課題)
- ルーターが「Web検索が要る質問かどうか」を自動判定する仕組み(今日はトグルによる手動ON/OFFのみ)

---

## ⑤ 成果物一覧(予定)

| ファイル | 新規/修正 | 役割 | 実行方法 | 出力先 |
|---|---|---|---|---|
| [[サポートAI作製計画/scripts/vad.py]] | 修正 | ①発話確定までの無音保持(1秒→3秒)。`silence_hold_sec`/`clock`を追加 | 単体実行はしない(`stt_engine.py`からimportして使う部品) | — |
| `scripts/tests/test_vad.py` | 修正 | ①のhold挙動・発話再開キャンセル・後方互換(hold=0)のテスト | `python -m pytest tests/test_vad.py -v` | 標準出力のみ |
| `scripts/wake_word.py` | 新規 | ②ウェイクワード「クレア/ねえクレア」の検出(表記ゆれ正規化+パターン照合)。10日目に削除したものの復活 | 単体実行はしない(`voice_gateway.py`からimportして使う部品) | — |
| `scripts/tests/test_wake_word.py` | 新規 | ②の検出/非検出・誤認識パターン・`text_after`切り出しのテスト | `python -m pytest tests/test_wake_word.py -v` | 標準出力のみ |
| [[サポートAI作製計画/scripts/voice_gateway.py]] | 修正 | ②STTのpartial/finalに対してウェイクワード判定し`wake_detected`をWS送信 / ④`web_search: true`の受け口 | `python voice_gateway.py --host 127.0.0.1 --port 5055` | なし(常駐サーバー)。応答時間は`results/response_timing.csv` |
| [[サポートAI作製計画/scripts/static/index.html]] | 修正 | ②ウェイクワード状態機械+視覚フィードバック / ③自動送信+キャンセル猶予 / トグルの`localStorage`永続化 | `voice_gateway.py`が配信。`http://127.0.0.1:5055/` | ブラウザ表示のみ(設定は`localStorage`) |
| `scripts/web_search.py` | 新規 | ④SearXNG(127.0.0.1:8888)を叩く検索部品+プロンプト整形 | `python -c "import web_search; ..."`(疎通確認時) | 標準出力のみ(ファイル保存はしない) |
| `scripts/tests/test_web_search.py` | 新規 | ④のマッピング・limit・バックエンド停止時フォールバックのテスト(HTTPはフェイク) | `python -m pytest tests/test_web_search.py -v` | 標準出力のみ |

---

## ⑥ 実機確認(①②③の回帰チェックリスト)

### 背景/目的

①はSTTの根幹(発話区切り)、②③はAIへ送信する経路そのものに手を入れるため、**壊すと「話しても何も起きない/勝手に送信される」という致命的な体験になる**。12日目③と同じく、**段階ごとに**チェックリストを通す(①実装後 → ②実装後 → ③実装後)。全部作ってから一度に確認しない。

### 事前準備

```powershell
# ターミナルA: 自作UIのサーバ
cd C:\Users\gakuh\Documents\obsidian\サポートAI作製計画\scripts
python voice_gateway.py --host 127.0.0.1 --port 5055
```

- Chromeで`http://127.0.0.1:5055/`を開き、DevToolsのConsole/NetworkタブでWS `/ws`を選択しておく
- **各ステップでスクリーンショットを残す**(12日目①で「記録し忘れ」により未確認項目が残った反省)

### チェックリスト

**①(3秒化)の確認**

- [x] マイクを開始し、「えーっと、(一拍おいて)明日の天気を教えて」と**途中で1秒程度の間を空けて**話す → 入力欄で文が分断されず1文としてつながる
- [x] 話し終えて黙ってから、**約3秒後**に確定転写(Whisper)が反映される
- [x] 3秒を超えて黙ったあとに話し始めると、別の発話として扱われる
- [x] マイク停止(⏹)時に、hold待ち中の発話が捨てられずに確定する(`flush()`の確認)

**②(ウェイクワード)の確認**

- [x] ウェイクワードトグルをONにすると、見た目が`on`になり、リロードしても維持される(`localStorage`)
- [x] ウェイクワードOFFのとき、従来どおり手動送信できる(退行なし)
- [x] 「クレア」と呼びかけると受付中の表示(`wake-armed`)になる
- [x] 「ねえクレア」「ねぇクレア」でも受け付ける
- [x] 呼びかけずに話した内容も**入力欄には必ず残る**(10日目⑦の「消える」問題が再発していない)
- [x] 受付中のまま15秒放置すると待機に戻る
- [ ] **AIの応答音声に「クレア」が含まれても自己トリガーしない**(応答再生中はマイクミュート。10日目①)

**③(自動送信)の確認**

- [ ] 自動送信ONのみ(ウェイクワードOFF)で、話す → 3秒黙る → 1.5秒後に自動送信される
- [ ] 自動送信の猶予中に入力欄をクリック/編集すると、送信が取り消される
- [ ] 応答再生中(speaking)に喋っても、その内容が割り込み送信されない
- [ ] 両方ON:「クレア、今何時?」→ 自動送信され、応答後は待機に戻る(次は再度呼びかけが必要)
- [ ] 同じ確定テキストが二重送信されない

**全体の退行確認(12日目③のチェックリストから主要項目のみ)**

- [x] 📎(文書アップロード)・📚(ナレッジ一覧)・📷(画像添付→`[route: DEEP]`)が従来どおり動く
- [ ] 状態遷移(idle → listening → thinking → speaking → idle)がバッジに反映される
- [ ] `results/response_timing.csv`の`duration_sec`が12日目①-3の実測(FASTで8.4〜8.8秒)から悪化していない ← **①の3秒hold追加とthinking修正の維持を同時に確認する項目**

### 結果 / 分析 / 改善策

**未実施。** ⑥はマイク入力・ブラウザ操作を伴う実機確認であり、今回の作業(コーディングエージェントのセッション)からは行えない。代わりに①②③の実装はすべてTDDで進め、ユニットテスト(`tests/test_vad.py`・`tests/test_stt_engine.py`・`tests/test_wake_word.py`・`tests/test_voice_gateway.py`のTestWakeWordWiring)で「サーバ側ロジック・WS配線」の範囲までは検証済み(全246件中245件pass。残り1件は本日の変更と無関係な12日目由来の既知failure、後述)。`index.html`側のJS(状態機械・自動送信スケジューリング)は`node --check`での構文検証のみ行い、実ブラウザでの動作(マイクリングの見た目・タイマーの体感等)は未確認。

なお全体テスト実行時に`tests/test_voice_gateway.py::TestDocumentEndpoints::test_upload_success_calls_ingest_and_returns_result`が500エラーで失敗することを確認したが、`git stash`で13日目の変更を退避しても(=12日目時点の状態でも)同じテストが失敗することを確認済みのため、**今日の①②③④の変更が原因ではない**(12日目のuncommitted変更に起因する既存不具合。14日目に予定されている📎添付ファイル機能の見直しと合わせて調査する)。

### 残課題

- 本チェックリスト(①②③の実機確認・全体の退行確認)は次回、実際にマイク・ブラウザを使って行うこと。特に①「3秒後に確定転写が反映される」の体感、②「呼んだのに反応しなかった」誤認識パターンの収集、③「自動送信の猶予中に編集して取り消せるか」は自動テストではカバーしきれない
- `TestDocumentEndpoints::test_upload_success_calls_ingest_and_returns_result`の500エラー(12日目由来、今日のスコープ外)は別途原因調査が必要

---

## 🐛 追記(2026-08-15):ウェイクワード「C.L.A.I.R.E.」スペルアウト表記の未検出バグを修正

実機画面のスクリーンショットで、`Hey, C.L.A.I.R.E.、明日の東京都の天気を調べる`のように**ウェイクワードがそのまま入力欄・送信テキストに残ってAIへ送られてしまう**不具合が見つかった。

**原因**: `wake_word.py`の`_normalize_for_match()`は文字数を変えない変換(NFKC/小文字化/ひらがな→カタカナ)に限定しているため、文字間のピリオドは除去されない。一方`WAKE_PATTERNS`の英字表記は`claire`/`clair`という**連続文字列**しか許容していなかったため、UIに表示される「C.L.A.I.R.E.」のように1文字ずつピリオドで区切られたスペルアウト表記だと`detect_wake_word()`が`None`を返し、`wake_detected`イベントがサーバから送られず、`index.html`側で`textInput.value`を`text_after`へ置き換える処理(1261行)が発火しないままだった。

**修正**: `WAKE_PATTERNS`の英字部分を`c\.?l\.?a\.?i\.?r\.?e\.?`/`c\.?l\.?a\.?i\.?r\.?`(各文字の直後に任意で`.`を許容)へ変更。`tests/test_wake_word.py`に`test_detects_dotted_spelled_out_ascii_name`を追加し、`Hey, C.L.A.I.R.E.、明日の東京都の天気を調べる` → `text_after == "明日の東京都の天気を調べる"`となることを確認した。全275件(5 subtests含む)pass。

実機(ブラウザ/マイク)での再現確認は未実施(引き続き⑥の実機確認待ち)。

---

## 🐛 追記(2026-08-15):括弧書き(原語併記等)の二重読みを解消

「スパイダーマン:ブランド・ニュー・デイ(Spider-Man: Brand New Day)」のように、直前の内容の原語表記・補足を括弧で添えている応答をTTSがそのまま読み上げると、同じ内容を2回言う形になりくどいと指摘があった。

**修正箇所**: [[サポートAI作製計画/scripts/sentence_splitter.py]]の`SentenceSplitter._normalize()`(読み上げ用テキスト正規化)に、全角`（）`・半角`()`の括弧書きを丸ごと除去する処理(`_RE_PAREN_FULLWIDTH`/`_RE_PAREN_HALFWIDTH`)を追加した。リンク記法`[label](url)`の`(url)`部分は、これより先に走る`_RE_LINK`/`_RE_IMAGE`で既に除去済みなので、括弧除去が二重に影響することはない。

**既知の制約(YAGNIで許容)**:
- 入れ子の括弧には対応しない(単純な非入れ子ケースのみ)
- コード片(`` `func(x, y)` ``のようなインラインコード)内の`()`もまとめて除去される。既存の`tests/test_sentence_splitter.py::test_inline_code_backticks_are_removed`はこの仕様変更を踏まえ、括弧を含まない識別子(`` `hello` ``)を使う形に更新した

**テスト**: `test_parenthesized_text_is_not_read_aloud`(半角括弧)・`test_fullwidth_parenthesized_text_is_not_read_aloud`(全角括弧)・`test_link_url_in_parens_is_still_dropped_correctly`(リンクとの非干渉)を新規追加。全278件(5 subtests含む)pass。

実機(音声再生)での聴感確認は未実施。

---

## 🐛 追記(2026-08-15):2ターン目以降、ウェイクワードとコマンドが連結される重大バグを修正

**報告された症状**(実機スクリーンショット付き):
1. 1ターン目はウェイクワード・自動送信とも正常。2ターン目以降、ウェイクワード「起動」を検出した直後の応答音声(「はい、ごうさま」)が**2回**鳴る
2. 2ターン目以降、入力欄・送信テキストに**ウェイクワードの文字(「起動」)がコマンドと混ざって**残る

**根本原因**: [[サポートAI作製計画/scripts/vad.py]]の`VoskEndpointVAD`(①で導入した3秒の無音保持)には「保留(hold)中に次の発話(非空partial)が来たら区切りを取り消して発話継続とみなす」仕様がある。ところが、ウェイクワード検出→(応答音声を聞く/次を話し始めるまでの)自然なポーズ→コマンド発話、という典型的な使い方は、このポーズがちょうど3秒未満に収まりやすい。その結果Voskの内部エンドポイントが「クレア起動」を区切ってもVAD側がholdで待ち、3秒以内に始まったコマンド発話を「発話継続」とみなして**1つの確定テキストへ連結してしまっていた**(`クレア起動`+`明日の天気は`→`クレア起動明日の天気は`)。

この連結された1つの確定テキストに対して`on_partial`(検出済み)と`on_final`(連結後に再度)の両方で`wake_word.detect_wake_word()`が走るため、`wake_detected`通知と応答音声が時間差で2回発火した(1回目は`WAKE_REPLY_COOLDOWN_SEC`=5秒のクールダウン内に収まらないタイミングで2回目が来ていた)。また、連結された音声をWhisperで再確定転写すると、Vosk側のライブ判定とは異なる書き起こし結果になることがあり、その場合`wake_word.py`の判定がずれて「起動」の文字がきれいに除去されずコマンド側に残ることがあった。

**修正**:
- [[サポートAI作製計画/scripts/stt_engine.py]]の`STTEngine`に`force_finalize_pending()`を新設。VADの無音保持を待たず、今たまっている音声をその場で強制確定させ、`self.recognizer.Reset()`(Vosk`KaldiRecognizer`本来のAPI。`vosk.KaldiRecognizer`に実在することを確認済み)でKaldi側の内部デコード状態も明示的に切り離す
- [[サポートAI作製計画/scripts/voice_gateway.py]]の`_check_wake_word()`で、ウェイクワード検出の瞬間に`stt.force_finalize_pending()`を呼ぶよう変更。これにより「クレア起動」はその場で確定・区切りが作られ、後続のコマンドは(Kaldi側もReset済みのため)ウェイクワードを含まない独立した発話として扱われる。連結自体が起きなくなるため、2重検出・文字混入のどちらも構造的に解消する
- `force_finalize_pending()`は内部で`on_final`を(強制確定した分について)再度呼ぶため、`_check_wake_word`が自分自身を再帰的に呼んでしまわないよう再入防止ガード(`_wake_force_finalizing`)を追加した

**テスト**: `tests/test_stt_engine.py::TestForceFinalizePending`(4件: hold中でも即確定/Recognizer.Resetが呼ばれる/次発話のバッファが前発話と混ざらない/何もなければno-op)、`tests/test_voice_gateway.py::TestWakeWordWiring`に2件追加(`force_finalize_pending()`が実際に呼ばれること/再入時に無限ループ・二重送信しないこと)。全284件(5 subtests含む)pass。

実機(マイクでの2ターン目以降の連続対話)での再現確認は未実施。特に「ウェイクワード直後に間を置かず話しかけた場合」の挙動を次回確認すること。

---

## 📌 次のステップ

> [!check] 次回ノート: [[サポートAI作製計画/14日目添付ファイルのピン留め指定とチャット履歴の永続化.md|14日目]]
> 下記3(📎添付ファイルの指定)と5の「UIの機能化の残り」のうちチャット履歴の永続化を主軸として引き継いだ。なお14日目の着手前調査により、**4(`test_upload_success_calls_ingest_and_returns_result`の500エラー)は解消済み**(全284件pass)、**5の「読み上げ速度バー」は実装済み**(ノートへの記載漏れ)であることが確認できたため、これらは14日目のスコープから外している。

> [!note] 更新(2026-08-15): ①〜④の完了状況を実機/コードで再確認
> ユーザーの実機確認により、2ターン目以降を含めてウェイクワード・自動送信が正常動作することを確認できた(前述の「2ターン目以降の連結バグ」修正が効いている)。また、コードベースを確認したところ④(Web検索)もその後の作業(コード内コメントに残る「14日目」)で`support_ai_auto_pipe.py`へのパイプライン結線・出典表示まで完了しており、`tests/test_support_ai_auto_pipe_web_search.py`でテスト済みだった(本ノート作成時点では「次回持ち越し」と書いていたが、その後のセッションで実施済み。ノートへの追記が漏れていた)。そのため①〜④はいずれも実装・実機確認まで完了とみなし、以下は**まだ終わっていないものだけ**を残す。

1. **⑥実機確認の残り項目**(①②③は主要フローの実機確認が取れたため、以下の個別チェックのみ残す):
   - **AIの応答音声に「クレア」が含まれても自己トリガーしないこと**(応答再生中のマイクミュートが効いているかの確認。10日目①)
   - **自動送信の1.5秒猶予中に入力欄をクリック/編集すると送信が取り消されること**
   - **応答再生中(speaking)に喋っても割り込み送信されないこと**
   - **同じ確定テキストが二重送信されないこと**
   - 状態遷移(idle → listening → thinking → speaking → idle)がバッジに正しく反映されること
   - `results/response_timing.csv`の`duration_sec`が12日目①-3の実測(FASTで8.4〜8.8秒)、および①の3秒hold追加後から悪化していないこと
2. **SearXNGの実ネットワーク疎通確認**(`curl ...&format=json`。④のパイプライン結線自体はコードレビュー・ユニットテストのみで確認済みだが、実際にSearXNGへ繋いで検索結果が返ることは未確認)。11日目のパッチ(`valkeydb.py`のpwd、`webutils.py`の2箇所)が`git pull`等で消えていないかも合わせて確認する
3. **14日目にやる**:📎添付ファイルを「このファイルについて答えて」と指定して使わせる機能(12日目までの試行で狙いどおり動かなかったため、`voice_gateway.py`の`attached_document`/`attached_document_text`の設計から見直す)
4. `tests/test_voice_gateway.py::TestDocumentEndpoints::test_upload_success_calls_ingest_and_returns_result`の500エラー(12日目由来、13日目のスコープ外と確認済み)は別途原因調査が必要
5. 引き続き持ち越し:
   - **UIの機能化の残り**:チャット履歴の永続化設計 → リネーム/削除/検索、読み上げ速度バー、モデル切替、思考モードトグル、引用元チップ(④の出典自体はテキストとして応答末尾に付くようになったが、専用UIの引用元チップはまだ)
   - **Web検索の自動判定**(今はトグルによる手動ON/OFFのみ。ルーターが「検索が要る質問か」を自動判定する仕組みは未着手)
   - **VRAM逼迫によるモデル入替スラッシング**(12日目①-3発見2。16GB VRAMにFAST 13GB+DEEP 15〜17GBを同時保持できない構造的制約)
   - **`run_turn()`の同期ブロッキング呼び出し**(12日目①-3残課題2。応答生成中にWS keepaliveが止まり接続断のリスク)
   - **複数枚画像の同時添付・OpenWebUI標準の`content` list形式対応**(YAGNIで保留)
   - エクセルやPowerPointの生成
1. [[サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md|9日目]]⑦(Tailscale Serveへの載せ替え・外出時CODEルート方針)は引き続き後回し
