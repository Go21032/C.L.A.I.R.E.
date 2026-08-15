"""
vad.py
--------
9日目ノート(サポートAI作製計画/9日目自前音声UIとストリーミング音声対話.md)
③残課題・⑥の部品。「VoskのResult()タイミングを発話区切りに流用できるか」の実装。

silero-vad/webrtcvadは音声波形そのものを解析して無音/発話を判定するライブラリだが、
③実機比較結果⑥で、Voskは`AcceptWaveform()`が`True`を返すタイミング(=Kaldi内部の
エンドポインタが「ここで区切ってよい」と判断したタイミング)を既に持っていることが
分かっている。これをそのまま発話区切りに流用できれば、silero-vad/webrtcvadの
導入(追加の依存・追加のCPU負荷・別途の閾値チューニング)が丸ごと不要になる。

`VoskEndpointVAD`はその検証結果を実装したもの。**音声そのものは一切扱わない**。
stt_engine.py がVoskの`KaldiRecognizer`を実際に呼んだ結果
(`accepted: bool` / 暫定テキスト / 確定テキスト)をこのクラスへ渡し、
「発話開始 (SPEECH_START)」「発話終了 (SPEECH_END)」のイベントに変換するだけの
薄い層にすることで、音声処理を一切importせずに単体テストできるようにしてある。

silero-vad/webrtcvadへの切り替えが必要になった場合は、同じ`observe()`インターフェース
(accepted/partial_text/final_textの代わりに、無音判定の結果を渡す形)を持つ別実装を
用意すれば`stt_engine.py`側は変更不要 — という差し替え可能性も設計上残してある。

13日目ノート①: Voskの内部エンドポインタは0.5〜1秒程度の無音で区切りを打つため、
「えーっと」と一拍置いただけで1つの発話が2〜3個のSPEECH_ENDに分断されていた。
`silence_hold_sec`(既定3秒)を追加し、Voskが区切りを検出しても即発火せず
`pending`状態にして保持する。保持中に次の発話(非空のpartial_text)が来たら
区切りを取り消して発話継続とみなし、`silence_hold_sec`だけ経過して初めて
SPEECH_ENDを発火する。Vosk/Kaldi側のエンドポインタ設定そのものは一切触らない
(バージョン/モデル差で挙動が変わるのを避け、「音声処理を一切importせずに単体
テストできる」という上記の設計上の利点を保ったまま実現するため)。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

# 13日目①: Voskの約0.5〜1秒のエンドポイント検出を3秒まで引き延ばす既定値。
# 「長すぎて反応が鈍い/短すぎて途中送信される」場合はここを調整する
# (調整したら13日目ノートの残課題欄に値と体感を記録すること)。
DEFAULT_SILENCE_HOLD_SEC = 3.0


class VadState(Enum):
    IDLE = "idle"
    SPEAKING = "speaking"


class VadEventType(Enum):
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"


@dataclass(frozen=True)
class VadEvent:
    type: VadEventType
    # SPEECH_ENDの場合のみ、Voskがそこまでに確定させたテキストを載せる。
    # あくまで暫定表示用の参考値であり、確定はstt_engine.pyがWhisperで取り直す。
    text: str = ""


class VoskEndpointVAD:
    """VoskのAcceptWaveform()の戻り値を発話区切りとして解釈するステートマシン。

    引数:
        min_utterance_chars: SPEECH_ENDとして扱う最小文字数。これ未満の確定テキスト
            (無音チャンクがたまたま区切られただけ、等)は発話とみなさず握りつぶす。
    """

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

    @property
    def state(self) -> VadState:
        return self._state

    def observe(
        self, *, accepted: bool, partial_text: str = "", final_text: str = ""
    ) -> list[VadEvent]:
        """stt_engine.pyが1チャンク分のVosk呼び出し結果を渡すたびに呼ぶ。

        - accepted=False: まだエンドポイントに達していない(発話継続中の暫定結果)。
          IDLE中に非空のpartial_textが来たら「発話開始」とみなす。非空partial_textが
          保留(hold)中に来た場合は、区切りを取り消して発話継続とみなす。
        - accepted=True: Voskの内部エンドポインタが区切りを検出した。
          SPEAKING中であれば即座にSPEECH_ENDにはせず`silence_hold_sec`だけ保留する。
          IDLE中(=発話が始まる前からの無音区切り)は無視する。
        - `silence_hold_sec`経過が`observe()`の呼び出しのたびに(accepted/partial_textの
          値に関わらず、無音チャンクが流れ続けている限り)チェックされ、経過していれば
          そこでSPEECH_ENDを発火する。
        """
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
                self._pending_since = None  # 発話再開: 区切りを取り消す
            elif self._state is VadState.IDLE:
                self._state = VadState.SPEAKING
                events.append(VadEvent(VadEventType.SPEECH_START))
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
        """ストリーム打ち切り(WebSocket切断など)後、次の発話に備えて状態を初期化する。"""
        self._state = VadState.IDLE
        self._pending_text.clear()
        self._pending_since = None
